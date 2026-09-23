#!/usr/bin/env python3
"""Desplegar el cabezal del DWARF mini (elevar altitud) y realizar una foto de prueba."""

import asyncio
import os
import cv2
import numpy as np
from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.device_profile import get_device_profile
from dwarf_alpaca.dwarf.session import configure_session, get_session, shutdown_session


def save_fits_as_jpg(fits_path: str, jpg_path: str):
    """Convierte el archivo FITS de 16 bits Bayer RGGB a imagen JPG visible en color."""
    try:
        with open(fits_path, "rb") as f:
            content = f.read()

        # Buscar el final del encabezado FITS (bloques de 2880 bytes)
        end_idx = content.find(b"END ")
        if end_idx == -1:
            header_size = 2880
        else:
            header_size = ((end_idx // 2880) + 1) * 2880

        raw_data = content[header_size:]
        # La imagen es 720x1280 uint16 big-endian
        img_16 = np.frombuffer(raw_data, dtype=">u2")
        if len(img_16) == 720 * 1280:
            img_16 = img_16.reshape((720, 1280))
        elif len(img_16) == 1080 * 1920:
            img_16 = img_16.reshape((1080, 1920))
        else:
            print(f"Dimensiones de buffer no reconocidas: {len(img_16)} píxeles")
            return

        # Ajuste de escala a 8 bits (histogram stretch básico)
        p_min = np.percentile(img_16, 1)
        p_max = np.percentile(img_16, 99.5)
        if p_max <= p_min:
            p_max = p_min + 1
        img_8 = np.clip((img_16 - p_min) * (255.0 / (p_max - p_min)), 0, 255).astype(np.uint8)

        # Demosaicing (Bayer RGGB a BGR)
        rgb_img = cv2.cvtColor(img_8, cv2.COLOR_BayerRG2BGR)
        cv2.imwrite(jpg_path, rgb_img)
        print(f"✅ Imagen convertida y guardada en: {jpg_path}")
    except Exception as e:
        print(f"Aviso al convertir FITS a JPG: {e}")


async def main():
    ip = "192.168.1.104"
    model = "dwarfmini"
    profile = get_device_profile(model)

    print(f"Conectando con DWARF mini en {ip}...")
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
    await session.acquire("camera")

    try:
        # 1. Desplegar el cabezal elevándolo en Altitud
        print("\n[1/3] 🚀 Elevando el cabezal del telescopio (eje de Altitud) durante 2.5 segundos...")
        await session.telescope_move_axis(axis=1, rate=12.0)
        await asyncio.sleep(2.5)
        print("Deteniendo movimiento de elevación...")
        await session.telescope_stop_axis(axis=1)
        print("✅ Cabezal desplegado y estabilizado.")

        await asyncio.sleep(1.0)

        # 2. Configurar y disparar exposición
        print("\n[2/3] 📸 Tomando fotografía de 1 segundo (Gain=60, Filtro=Astro)...")
        await session.camera_connect()
        state = session.camera_state
        state.requested_gain = 60
        state.filter_name = "Astro"
        state.filter_index = 0
        state.requested_bin = (1, 1)
        state.requested_frame_count = 1

        await session.camera_start_exposure(1.0, light=True, continue_without_darks=True)
        if state.capture_task is not None:
            await asyncio.wait_for(asyncio.shield(state.capture_task), timeout=60.0)

        print(f"✅ Exposición finalizada. Archivo remoto: {state.retrieved_file_path}")

        # 3. Descargar y guardar en local
        print("\n[3/3] 💾 Descargando FITS y generando vista previa...")
        os.makedirs("captures", exist_ok=True)
        local_fits = "captures/desplegado_test.fits"
        local_jpg = "captures/desplegado_test.jpg"

        import ftplib
        ftp = ftplib.FTP(ip, timeout=10)
        ftp.login()
        with open(local_fits, "wb") as f:
            ftp.retrbinary(f"RETR {state.retrieved_file_path}", f.write)
        ftp.quit()

        print(f"✅ FITS guardado en: {local_fits} ({os.path.getsize(local_fits):,} bytes)")
        save_fits_as_jpg(local_fits, local_jpg)

    finally:
        with asyncio.CancelledError:
            pass
        await session.release("camera")
        await session.release("telescope")
        await shutdown_session()
        print("\nSesión cerrada limpiamente.")


if __name__ == "__main__":
    asyncio.run(main())

