#!/usr/bin/env python3
"""Script para controlar la inclinación (altitud) del DWARF mini y disparar fotos."""

import argparse
import asyncio
import os
import cv2
import numpy as np
from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.device_profile import get_device_profile
from dwarf_alpaca.dwarf.session import configure_session, get_session, shutdown_session


def save_fits_as_jpg(fits_path: str, jpg_path: str):
    """Convierte el FITS del DWARF mini a JPG en color."""
    try:
        with open(fits_path, "rb") as f:
            content = f.read()

        end_idx = content.find(b"END ")
        header_size = ((end_idx // 2880) + 1) * 2880
        raw_i2 = np.frombuffer(content[header_size:], dtype=">i2")
        data = raw_i2.astype(np.float32) + 32768.0

        if len(data) == 720 * 1280:
            img = data.reshape((720, 1280))
        elif len(data) == 1080 * 1920:
            img = data.reshape((1080, 1920))
        else:
            return

        p1, p99 = np.percentile(img, (0.5, 99.5))
        norm = np.clip((img - p1) / max(1.0, (p99 - p1)) * 255.0, 0, 255).astype(np.uint8)
        rgb = cv2.cvtColor(norm, cv2.COLOR_BayerRG2BGR)
        cv2.imwrite(jpg_path, rgb)
        print(f"🖼️  Vista previa guardada en: {jpg_path}")
    except Exception as e:
        print(f"Aviso al convertir FITS: {e}")


async def run(args):
    ip = args.ip
    model = "dwarfmini"
    profile = get_device_profile(model)

    settings = Settings(
        dwarf_ap_ip=ip,
        dwarf_device_model=model,
        dwarf_ws_client_id=profile.ws_client_id,
        network_mode="sta",
        calibrate_after_server_start=False,
        auto_calibrate_on_slew=False,
        allow_continue_without_darks=True,
        dwarf_mini_capture_mode="astro",
    )
    configure_session(settings)
    session = await get_session()
    await session.acquire("telescope")
    if args.shoot:
        await session.acquire("camera")

    try:
        # Movimiento de Altitud
        if args.up and args.up > 0:
            print(f"⬆️  Elevando cabezal durante {args.up:.1f}s a velocidad {args.speed:.0f}...")
            await session.telescope_move_axis(axis=1, rate=args.speed)
            await asyncio.sleep(args.up)
            await session.telescope_stop_axis(axis=1)
            print("⏹️  Movimiento detenido.")
            await asyncio.sleep(0.5)

        elif args.down and args.down > 0:
            print(f"⬇️  Bajando cabezal durante {args.down:.1f}s a velocidad {args.speed:.0f}...")
            await session.telescope_move_axis(axis=1, rate=-args.speed)
            await asyncio.sleep(args.down)
            await session.telescope_stop_axis(axis=1)
            print("⏹️  Movimiento detenido.")
            await asyncio.sleep(0.5)

        # Disparo opcional
        if args.shoot:
            exp_time = max(1.0, float(args.shoot))
            gain = int(args.gain)
            print(f"\n📸 Realizando captura de {exp_time:.1f}s (Gain={gain}, Filtro={args.filter})...")
            await session.camera_connect()
            state = session.camera_state
            state.requested_gain = gain
            state.filter_name = args.filter
            state.filter_index = 0 if args.filter == "Astro" else 1
            state.requested_bin = (1, 1)
            state.requested_frame_count = 1

            await session.camera_start_exposure(exp_time, light=True, continue_without_darks=True)
            if state.capture_task is not None:
                await asyncio.wait_for(asyncio.shield(state.capture_task), timeout=60.0)

            os.makedirs("captures", exist_ok=True)
            local_fits = "captures/inclinado.fits"
            local_jpg = "captures/inclinado.jpg"

            import ftplib
            ftp = ftplib.FTP(ip, timeout=10)
            ftp.login()
            with open(local_fits, "wb") as f:
                ftp.retrbinary(f"RETR {state.retrieved_file_path}", f.write)
            ftp.quit()

            print(f"💾 FITS descargado: {local_fits} ({os.path.getsize(local_fits):,} bytes)")
            save_fits_as_jpg(local_fits, local_jpg)

    finally:
        if args.shoot:
            await session.release("camera")
        await session.release("telescope")
        await shutdown_session()


def main():
    parser = argparse.ArgumentParser(description="Control de inclinación y captura DWARF mini")
    parser.add_argument("--ip", default="192.168.1.104", help="IP del DWARF")
    parser.add_argument("--up", type=float, help="Segundos para elevar el cabezal")
    parser.add_argument("--down", type=float, help="Segundos para bajar el cabezal")
    parser.add_argument("--speed", type=float, default=28.0, help="Velocidad (0 a 30, defecto: 28)")
    parser.add_argument("--shoot", type=float, help="Tomar foto de X segundos tras moverlo", default=None)
    parser.add_argument("--gain", type=int, default=60, help="Ganancia de la cámara")
    parser.add_argument("--filter", default="Astro", choices=["Astro", "Duo-Band"], help="Filtro")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()

