#!/usr/bin/env python3
"""Script para tomar fotos con la lente gran angular (wide), teleobjetivo (tele) o ambas sin mover el telescopio."""

import argparse
import asyncio
import ftplib
import os
import sys
from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.device_profile import get_device_profile
from dwarf_alpaca.dwarf.session import configure_session, get_session, shutdown_session
from dwarf_alpaca.proto import protocol_pb2
from dwarf_alpaca.proto.camera_pb2 import ReqPhoto
from dwarf_alpaca.proto.base_pb2 import ComResponse


async def capture_camera(session, cam_type: str, ip: str, output_dir: str):
    if cam_type == "wide":
        module_id = protocol_pb2.ModuleId.MODULE_CAMERA_WIDE
        cmd_id = protocol_pb2.DwarfCMD.CMD_CAMERA_WIDE_PHOTOGRAPH
        label = "Gran Angular (Wide)"
        prefix = "WIDE"
    else:
        module_id = protocol_pb2.ModuleId.MODULE_CAMERA_TELE
        cmd_id = protocol_pb2.DwarfCMD.CMD_CAMERA_TELE_PHOTOGRAPH
        label = "Teleobjetivo (Tele)"
        prefix = "TELE"

    print(f"📸 Disparando foto con la cámara {label}...")
    res = await session._send_request(
        module_id,
        cmd_id,
        ReqPhoto(),
        ComResponse,
        timeout=8.0
    )
    if res.code != 0:
        print(f"❌ Error al disparar cámara {cam_type}: código {res.code}")
        return None

    # Esperar a que el firmware escriba la imagen en almacenamiento
    await asyncio.sleep(1.5)

    # Descargar la foto más reciente por FTP
    ftp = ftplib.FTP(ip, timeout=10)
    ftp.login()
    photos = ftp.nlst("/Normal_Photos")
    matching = [p for p in photos if prefix in p and p.endswith(".jpg")]
    if not matching:
        print(f"⚠️ No se encontró foto reciente con prefijo {prefix} en /Normal_Photos")
        ftp.quit()
        return None

    latest_remote = matching[-1]
    local_filename = f"foto_{cam_type}.jpg"
    local_path = os.path.join(output_dir, local_filename)

    with open(local_path, "wb") as f:
        ftp.retrbinary(f"RETR {latest_remote}", f.write)
    ftp.quit()

    file_size = os.path.getsize(local_path)
    print(f"✅ Foto {cam_type.upper()} guardada en: {local_path} ({file_size:,} bytes)")
    return local_path


async def main():
    parser = argparse.ArgumentParser(description="Disparador de fotos DWARF Mini (Tele / Wide)")
    parser.add_argument("--ip", default="192.168.1.104", help="Dirección IP del telescopio")
    parser.add_argument("--camera", default="both", choices=["tele", "wide", "both"], help="Cámara a disparar")
    parser.add_argument("--up", type=float, default=0.0, help="Segundos para elevar el cabezal antes de disparar")
    parser.add_argument("--down", type=float, default=0.0, help="Segundos para bajar el cabezal antes de disparar")
    parser.add_argument("--speed", type=float, default=28.0, help="Velocidad de movimiento")
    parser.add_argument("--output-dir", default="captures", help="Directorio destino para las fotos")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    profile = get_device_profile("dwarfmini")
    settings = Settings(
        dwarf_ap_ip=args.ip,
        dwarf_device_model="dwarfmini",
        dwarf_ws_client_id=profile.ws_client_id,
        network_mode="sta",
        calibrate_after_server_start=False,
        auto_calibrate_on_slew=False,
    )
    configure_session(settings)
    session = await get_session()
    await session.acquire("camera")

    try:
        # Movimiento opcional
        if args.up > 0:
            print(f"⬆️ Elevando cabezal {args.up:.1f}s...")
            await session.telescope_move_axis(axis=1, rate=args.speed)
            await asyncio.sleep(args.up)
            await session.telescope_stop_axis(axis=1)
            await asyncio.sleep(0.5)
        elif args.down > 0:
            print(f"⬇️ Bajando cabezal {args.down:.1f}s...")
            await session.telescope_move_axis(axis=1, rate=-args.speed)
            await asyncio.sleep(args.down)
            await session.telescope_stop_axis(axis=1)
            await asyncio.sleep(0.5)

        # Captura según selección
        if args.camera in ("wide", "both"):
            await capture_camera(session, "wide", args.ip, args.output_dir)
            if args.camera == "both":
                await asyncio.sleep(1.0)
        if args.camera in ("tele", "both"):
            await capture_camera(session, "tele", args.ip, args.output_dir)

    finally:
        await session.release("camera")
        await shutdown_session()


if __name__ == "__main__":
    asyncio.run(main())

