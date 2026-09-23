#!/usr/bin/env python3
"""
dwarf_mockup.py - Servidor simulador de DWARF Mini para pruebas de Scheduling

Emula el servidor WebSocket del DWARF Mini (puerto 9900) respondiendo al protocolo
binario Protobuf (WsPacket) para las operaciones de planificaciones:
  - CMD 13000: Sincronización de hora (ReqSetTime)
  - CMD 16102: Listar todos los planes (ReqGetAllShootingSchedule)
  - CMD 16100: Sincronizar/crear plan (ReqSyncShootingSchedule)
  - CMD 16108: Borrar plan (ReqDeleteShootingSchedule)
  - CMD 16101: Cancelar plan (ReqCancelShootingSchedule)

Carga inicialmente los datos desde dwarf_schedules_backup.json.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import signal
import sys
import time
from typing import Dict, List, Optional

import structlog
from google.protobuf.message import DecodeError
import websockets
from websockets.asyncio.server import ServerConnection, serve

# Añadir src al path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from dwarf_alpaca.proto import base_pb2, protocol_pb2, shooting_schedule_pb2
from dwarf_alpaca.proto.system_pb2 import ReqSetTime

logger = structlog.get_logger(__name__)

# Módulos y Comandos
MODULE_SYSTEM = 1
MODULE_SHOOTING_SCHEDULE = 13  # En el enum protobuf MODULE_SHOOTING_SCHEDULE = 13

CMD_SET_TIME = 13000
CMD_SYNC_SCHEDULE = 16100
CMD_CANCEL_SCHEDULE = 16101
CMD_GET_ALL_SCHEDULES = 16102
CMD_GET_SCHEDULE_BY_ID = 16103
CMD_GET_TASK_BY_ID = 16104
CMD_DELETE_SCHEDULE = 16108


class DwarfMockDatabase:
    """Base de datos en memoria para el simulador de planificaciones del DWARF."""

    def __init__(self, backup_file: str):
        self.backup_file = backup_file
        self.schedules: List[shooting_schedule_pb2.ShootingScheduleMsg] = []
        self.load_from_backup()

    def load_from_backup(self) -> None:
        if not os.path.exists(self.backup_file):
            print(f"⚠️ Archivo de respaldo '{self.backup_file}' no encontrado. Iniciando con BD vacía.")
            return

        with open(self.backup_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        schedules_list = data.get("schedules", [])
        print(f"📦 Cargando {len(schedules_list)} planes desde {self.backup_file}...")

        for s in schedules_list:
            msg = shooting_schedule_pb2.ShootingScheduleMsg()
            msg.schedule_id = s.get("schedule_id", f"mock-{int(time.time()*1000)}")
            msg.schedule_name = s.get("schedule_name", "Plan")
            msg.device_id = 4
            msg.mac_address = "40:FD:F3:1A:37:6C"
            msg.start_time = int(s.get("start_timestamp", 0))
            msg.end_time = int(s.get("end_timestamp", 0))
            msg.result = s.get("result_code", 1)  # SUCCESS por defecto
            msg.created_time = int(s.get("start_timestamp", time.time()))
            msg.updated_time = int(s.get("end_timestamp", time.time()))
            msg.state = s.get("state_code", 3)   # COMPLETED por defecto
            msg.param_mode = 0
            msg.param_version = 1
            msg.schedule_time = msg.created_time
            msg.sync_state = 1

            loc = s.get("location", {})
            params_dict = {
                "calibrationMode": 0 if s.get("auto_calibrate") else 1,
                "focusMode": 0 if s.get("auto_focus") else 1,
                "cityName": loc.get("city", "Alcobendas"),
                "latitude": loc.get("latitude", 40.55),
                "longitude": loc.get("longitude", -3.65),
            }
            msg.params = json.dumps(params_dict)

            # Tareas
            for t_idx, t in enumerate(s.get("tasks", []), 1):
                t_msg = msg.shooting_tasks.add()
                t_msg.schedule_id = msg.schedule_id
                t_msg.schedule_task_id = f"{msg.schedule_id}-T{t_idx}"
                t_msg.state = 2  # SUCCESS
                t_msg.param_mode = 0
                t_msg.param_version = 1
                t_msg.create_from = 1
                t_msg.created_time = msg.created_time
                t_msg.updated_time = msg.updated_time

                mos = t.get("mosaic", {})
                is_mos = bool(mos.get("enabled", False))
                h_sc = int(mos.get("horizontal_scale", 100))
                v_sc = int(mos.get("vertical_scale", 100))
                rot = int(mos.get("rotation", -1))

                exp_val = t.get("exp", 15)
                gain_val = t.get("gain", 60)
                filt_str = t.get("filter", "Astro")
                filt_idx = 2 if filt_str.lower() in ("duo-band", "duoband") else 1

                task_params = {
                    "name": t.get("name", "Target"),
                    "ra": float(t.get("ra_hours", 0.0)),
                    "dec": float(t.get("dec_degrees", 0.0)),
                    "startTime": msg.start_time,
                    "endTime": msg.end_time,
                    "shutterIndex": 159 if exp_val == 30 else (156 if exp_val == 15 else 162),
                    "shutterName": str(exp_val),
                    "gainIndex": gain_val,
                    "gainName": str(gain_val),
                    "filterModeIndex": filt_idx,
                    "filterModeName": filt_str,
                    "isMosaicMode": is_mos,
                    "horizontalScale": h_sc,
                    "verticalScale": v_sc,
                    "rotation": rot,
                    "stacked": 0,
                    "count": 0,
                    "atlasSortTypeValue": 0,
                    "cometQueryName": "",
                    "wellknownName": "",
                }
                t_msg.params = json.dumps(task_params)

            self.schedules.append(msg)

        print(f"✅ {len(self.schedules)} planes cargados en memoria listos para servir.")

    def get_all(self) -> List[shooting_schedule_pb2.ShootingScheduleMsg]:
        return list(self.schedules)

    def sync_schedule(self, new_sched: shooting_schedule_pb2.ShootingScheduleMsg) -> shooting_schedule_pb2.ShootingScheduleMsg:
        # Si ya existe por ID o por nombre, reemplazarlo
        existing_idx = next((i for i, s in enumerate(self.schedules) if s.schedule_id == new_sched.schedule_id or s.schedule_name == new_sched.schedule_name), None)
        if existing_idx is not None:
            self.schedules[existing_idx] = new_sched
            print(f"🔄 Plan '{new_sched.schedule_name}' actualizado en memoria.")
        else:
            self.schedules.insert(0, new_sched)
            print(f"✨ Nuevo plan '{new_sched.schedule_name}' guardado en memoria ({len(new_sched.shooting_tasks)} tareas).")
        return new_sched

    def delete_schedule(self, sched_id: str) -> bool:
        initial_len = len(self.schedules)
        self.schedules = [s for s in self.schedules if s.schedule_id != sched_id and s.schedule_name != sched_id]
        deleted = len(self.schedules) < initial_len
        if deleted:
            print(f"🗑️ Plan '{sched_id}' eliminado con éxito de memoria.")
        else:
            print(f"⚠️ No se encontró el plan '{sched_id}' para borrar.")
        return deleted


class DwarfMockServer:
    """Servidor WebSocket emulador de DWARF."""

    def __init__(self, host: str, port: int, db: DwarfMockDatabase):
        self.host = host
        self.port = port
        self.db = db
        self.active_clients = set()

    async def handle_connection(self, websocket: ServerConnection):
        client_addr = websocket.remote_address
        print(f"\n📡 [CONNECT] Nuevo cliente conectado desde {client_addr}")
        self.active_clients.add(websocket)

        try:
            async for raw_data in websocket:
                if not isinstance(raw_data, bytes):
                    continue

                packet = base_pb2.WsPacket()
                try:
                    packet.ParseFromString(raw_data)
                except DecodeError:
                    print("⚠️ Paquete recibido con formato no Protobuf.")
                    continue

                await self.dispatch_packet(websocket, packet)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.active_clients.discard(websocket)
            print(f"🔌 [DISCONNECT] Cliente {client_addr} desconectado.")

    async def dispatch_packet(self, websocket: ServerConnection, packet: base_pb2.WsPacket):
        cmd = packet.cmd
        module_id = packet.module_id
        client_id = packet.client_id or ""

        # CMD 13000: ReqSetTime
        if cmd == CMD_SET_TIME:
            print("🕒 [CMD 13000] Sincronización de hora recibida.")
            res = base_pb2.ComResponse(code=0)
            await self.send_response(websocket, module_id, cmd, res, client_id)
            return

        # CMD 16102: ReqGetAllShootingSchedule
        if cmd == CMD_GET_ALL_SCHEDULES:
            schedules = self.db.get_all()
            print(f"📋 [CMD 16102] Solicitud de todos los planes. Enviando {len(schedules)} planes...")
            res = shooting_schedule_pb2.ResGetAllShootingSchedule(code=0)
            res.shooting_schedule.extend(schedules)
            await self.send_response(websocket, module_id, cmd, res, client_id)
            return

        # CMD 16100: ReqSyncShootingSchedule
        if cmd == CMD_SYNC_SCHEDULE:
            req = shooting_schedule_pb2.ReqSyncShootingSchedule()
            req.ParseFromString(packet.data)
            saved_sched = self.db.sync_schedule(req.shooting_schedule)
            print(f"📥 [CMD 16100] Plan '{saved_sched.schedule_name}' sincronizado con éxito.")
            res = shooting_schedule_pb2.ResSyncShootingSchedule(
                code=0,
                can_replace=True,
                shooting_schedule=saved_sched
            )
            await self.send_response(websocket, module_id, cmd, res, client_id)
            return

        # CMD 16108: ReqDeleteShootingSchedule
        if cmd == CMD_DELETE_SCHEDULE:
            req = shooting_schedule_pb2.ReqDeleteShootingSchedule()
            req.ParseFromString(packet.data)
            sched_id = req.id
            print(f"🗑️ [CMD 16108] Solicitud de borrado de plan ID: {sched_id}")
            self.db.delete_schedule(sched_id)
            res = shooting_schedule_pb2.ResDeleteShootingSchedule(
                id=sched_id,
                code=0
            )
            await self.send_response(websocket, module_id, cmd, res, client_id)
            return

        # CMD 16101: ReqCancelShootingSchedule
        if cmd == CMD_CANCEL_SCHEDULE:
            req = shooting_schedule_pb2.ReqCancelShootingSchedule()
            req.ParseFromString(packet.data)
            sched_id = req.id
            print(f"⏹️ [CMD 16101] Solicitud de cancelar plan ID: {sched_id}")
            res = shooting_schedule_pb2.ResCancelShootingSchedule(
                id=sched_id,
                code=0
            )
            await self.send_response(websocket, module_id, cmd, res, client_id)
            return

        # Comando desconocido
        print(f"❓ [CMD {cmd} en Módulo {module_id}] Comando no emulado. Respondiendo OK genérico.")
        res = base_pb2.ComResponse(code=0)
        await self.send_response(websocket, module_id, cmd, res, client_id)

    async def send_response(self, websocket: ServerConnection, module_id: int, cmd: int, message, client_id: str):
        resp_packet = base_pb2.WsPacket(
            major_version=1,
            minor_version=9,
            device_id=4,
            module_id=module_id,
            cmd=cmd,
            type=1,  # TYPE_REQUEST_RESPONSE
            data=message.SerializeToString(),
            client_id=client_id,
        )
        await websocket.send(resp_packet.SerializeToString())


async def main_async():
    parser = argparse.ArgumentParser(description="DWARF Mini Simulator (dwarf_mockup)")
    parser.add_argument("--port", type=int, default=9900, help="Puerto WebSocket (por defecto 9900)")
    parser.add_argument("--host", default="0.0.0.0", help="Host de escucha (por defecto 0.0.0.0)")
    parser.add_argument(
        "--backup",
        default=os.path.join(BASE_DIR, "dwarf_schedules_backup.json"),
        help="Archivo JSON con el respaldo inicial de planes",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("🔭 SIMULADOR DE DWARF MINI (dwarf_mockup) INICIADO")
    print("=" * 60)
    print(f"🌐 Escuchando en ws://{args.host}:{args.port}")
    print(f"📂 Archivo de datos: {args.backup}")

    db = DwarfMockDatabase(args.backup)
    mock_server = DwarfMockServer(args.host, args.port, db)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    async with serve(mock_server.handle_connection, args.host, args.port):
        print("\n🚀 Servidor activo y esperando conexiones... (Presiona Ctrl+C para detener)")
        await stop_event.wait()

    print("\n🛑 Servidor DWARF Mockup detenido limpiamente.")


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

