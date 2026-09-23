#!/usr/bin/env python3
"""Script de comprobación rápida de conexión con un telescopio DWARF en la red local."""

import argparse
import asyncio
import sys

from dwarf_alpaca.device_profile import get_device_profile
from dwarf_alpaca.dwarf.ws_client import DwarfWsClient
from dwarf_alpaca.proto import protocol_pb2
from dwarf_alpaca.proto.task_center_pb2 import ReqGetDeviceStateInfo, ResGetDeviceStateInfo


async def probe_port(ip: str, port: int, timeout: float = 1.0) -> bool:
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        w.close()
        await w.wait_closed()
        return True
    except Exception:
        return False


async def discover_ip() -> str | None:
    # Probar primero la IP conocida
    if await probe_port("192.168.1.104", 9900, timeout=0.5):
        return "192.168.1.104"

    # Escaneo rápido de la subred /24
    found = []

    async def check(candidate: str):
        if await probe_port(candidate, 9900, timeout=0.6):
            found.append(candidate)

    tasks = [check(f"192.168.1.{i}") for i in range(1, 255)]
    await asyncio.gather(*tasks)
    return found[0] if found else None


async def run_check(ip: str, model: str = "dwarfmini") -> int:
    print(f"==================================================")
    print(f" Comprobando conexión con DWARF ({model}) en {ip}")
    print(f"==================================================")

    # 1. Chequeo de puertos
    ports = {
        9900: "WebSocket (Control / Protocol Buffers)",
        8082: "HTTP (API de fotos y medios)",
        21: "FTP (Transferencia de archivos FITS/imágenes)",
        554: "RTSP (Stream de vídeo en directo)",
    }

    print("\n[1/2] Verificando puertos de red...")
    for port, desc in ports.items():
        open_status = await probe_port(ip, port)
        status_str = "✅ ABIERTO" if open_status else "❌ CERRADO"
        print(f"  - Puerto {port:<5} ({desc}): {status_str}")

    # 2. Conexión WebSocket y lectura de estado
    print("\n[2/2] Conectando protocolo WebSocket...")
    profile = get_device_profile(model)
    client = DwarfWsClient(
        ip,
        major_version=profile.protocol.ws_major_version,
        minor_version=profile.protocol.ws_minor_version,
        device_id=profile.protocol.ws_device_id,
        client_id=profile.ws_client_id,
    )

    try:
        await client.connect()
        print("  ✅ Conexión WebSocket establecida con éxito.")

        response = await client.send_request(
            protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
            protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_GET_DEVICE_STATE_INFO,
            ReqGetDeviceStateInfo(),
            ResGetDeviceStateInfo,
            timeout=8.0,
        )

        dev_state = response.device_state_info
        tele = response.tele_camera_state_info
        wide = response.wide_camera_state_info
        focus = response.focus_motor_state_info

        print("\n--- Estado recibido del DWARF Mini ---")
        print(f"  🔋 Batería:             {dev_state.battery_info.percentage}%")
        print(f"  💾 Almacenamiento:      {dev_state.storage_info.available_size} GB libres / {dev_state.storage_info.total_size} GB totales")
        print(f"  🔭 Enfoque (posición):   {focus.focus_position.pos}")
        print(f"  📷 Cámara Teleobjetivo: {tele.resolution_width}x{tele.resolution_height} (FOV: {tele.h_fov:.2f}° x {tele.v_fov:.2f}°)")
        print(f"  📷 Cámara Gran Angular: {wide.resolution_width}x{wide.resolution_height} (FOV: {wide.h_fov:.2f}° x {wide.v_fov:.2f}°)")
        print(f"  💡 Indicador de energía:{' Encendido' if dev_state.power_ind_state.state else ' Apagado'}")
        print("---------------------------------------")

        await client.close()
        print("\n✅ ¡Prueba de conexión superada con éxito!")
        return 0

    except Exception as e:
        print(f"\n❌ Error al comunicarse con el telescopio: {e}")
        return 1


async def main() -> int:
    parser = argparse.ArgumentParser(description="Comprobar conexión con DWARF mini")
    parser.add_argument("--ip", help="Dirección IP del DWARF (por defecto busca automáticamente)", default=None)
    parser.add_argument("--model", help="Modelo del dispositivo", default="dwarfmini")
    args = parser.parse_args()

    ip = args.ip
    if not ip:
        print("Buscando DWARF en la red local...")
        ip = await discover_ip()
        if not ip:
            print("❌ No se encontró ningún DWARF con el puerto 9900 abierto en la red local.")
            return 1
        print(f"Detectado DWARF en: {ip}")

    return await run_check(ip, args.model)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

