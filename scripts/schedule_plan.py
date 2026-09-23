#!/usr/bin/env python3
"""Script para programar e inyectar sesiones directamente en el firmware del DWARF Mini."""

import argparse
import asyncio
import datetime
import json
import time
import uuid

from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.dwarf.session import configure_session, get_session
from dwarf_alpaca.proto import protocol_pb2, shooting_schedule_pb2


def parse_ra(ra_str: str) -> float:
    parts = [float(p) for p in ra_str.split(":")]
    h = parts[0]
    m = parts[1] if len(parts) > 1 else 0.0
    s = parts[2] if len(parts) > 2 else 0.0
    return h + m / 60.0 + s / 3600.0


def parse_dec(dec_str: str) -> float:
    sign = -1.0 if dec_str.strip().startswith("-") else 1.0
    cleaned = dec_str.strip().lstrip("+-")
    parts = [float(p) for p in cleaned.split(":")]
    d = parts[0]
    m = parts[1] if len(parts) > 1 else 0.0
    s = parts[2] if len(parts) > 2 else 0.0
    return sign * (d + m / 60.0 + s / 3600.0)


async def main():
    parser = argparse.ArgumentParser(description="Programar sesión en DWARF Mini")
    parser.add_argument("--ip", default="192.168.1.104", help="IP del DWARF")
    parser.add_argument("--name", default="macos01", help="Nombre de la planificación")
    parser.add_argument("--target", default="C43", help="Nombre del objeto")
    parser.add_argument("--ra", default="00:03:15", help="Ascensión Recta (HH:MM:SS)")
    parser.add_argument("--dec", default="+16:08:44", help="Declinación (+/-DD:MM:SS)")
    parser.add_argument("--start", default="21:30", help="Hora de inicio local (HH:MM)")
    parser.add_argument("--end", default="00:00", help="Hora de fin local (HH:MM)")
    parser.add_argument("--exp", type=int, default=30, help="Tiempo de exposición en segundos")
    parser.add_argument("--gain", type=int, default=60, help="Ganancia")
    parser.add_argument("--filter", default="Astro", choices=["Astro", "Duo-Band"], help="Filtro")
    args = parser.parse_args()

    settings = Settings(dwarf_ap_ip=args.ip, dwarf_device_model="dwarfmini")
    configure_session(settings)
    session = await get_session()
    await session._ensure_ws()

    # Obtener configuración del dispositivo desde los planes existentes
    req_all = shooting_schedule_pb2.ReqGetAllShootingSchedule()
    resp_all = await session._send_request(
        protocol_pb2.ModuleId.MODULE_SHOOTING_SCHEDULE,
        16102,
        req_all,
        shooting_schedule_pb2.ResGetAllShootingSchedule,
        timeout=5.0,
    )
    last_s = resp_all.shooting_schedule[0] if resp_all.shooting_schedule else None
    device_id = last_s.device_id if last_s else 4
    mac_address = last_s.mac_address if last_s else "40:FD:F3:1A:37:6C"
    main_params = last_s.params if last_s else json.dumps({
        "calibrationMode": 1,
        "cityName": "Alcobendas",
        "focusMode": 1,
        "latitude": 40.55,
        "longitude": -3.65
    })

    # Calcular timestamps
    tz = datetime.datetime.now().astimezone().tzinfo
    today = datetime.date.today()
    s_h, s_m = [int(p) for p in args.start.split(":")]
    e_h, e_m = [int(p) for p in args.end.split(":")]

    dt_start = datetime.datetime(today.year, today.month, today.day, s_h, s_m, 0, tzinfo=tz)
    # Si la hora de fin es menor o igual, asumimos que pasa de medianoche (mañana)
    if (e_h, e_m) <= (s_h, s_m):
        tomorrow = today + datetime.timedelta(days=1)
        dt_end = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, e_h, e_m, 0, tzinfo=tz)
    else:
        dt_end = datetime.datetime(today.year, today.month, today.day, e_h, e_m, 0, tzinfo=tz)

    start_ts = int(dt_start.timestamp())
    end_ts = int(dt_end.timestamp())
    now_ts = int(time.time())

    ra_hours = parse_ra(args.ra)
    dec_deg = parse_dec(args.dec)

    uid_prefix = "f26ca2d6-57c3-40d0-86fd-ee854e007474"
    sched_id = f"{uid_prefix}{int(now_ts*1000)}.MacOS"
    task_id = f"{uid_prefix}{int(now_ts*1000 + 100)}"

    filter_index = 1 if args.filter.lower() == "astro" else 2

    # Shutter index mapping habitual (30s = 159, 60s = 162, 90s = 163, 15s = 156)
    shutter_map = {15: 156, 30: 159, 60: 162, 90: 163}
    shutter_index = shutter_map.get(args.exp, 159)

    task_params = {
        "atlasSortTypeValue": 0,
        "cometQueryName": "",
        "count": 0,
        "dec": dec_deg,
        "endTime": end_ts,
        "filterModeIndex": filter_index,
        "filterModeName": args.filter,
        "gainIndex": args.gain,
        "gainName": str(args.gain),
        "horizontalScale": 100,
        "isMosaicMode": False,
        "name": args.target,
        "ra": ra_hours,
        "rotation": -1,
        "shutterIndex": shutter_index,
        "shutterName": str(args.exp),
        "stacked": 0,
        "startTime": start_ts,
        "verticalScale": 100,
        "wellknownName": "",
    }

    sched_msg = shooting_schedule_pb2.ShootingScheduleMsg(
        schedule_id=sched_id,
        schedule_name=args.name,
        device_id=device_id,
        mac_address=mac_address,
        start_time=start_ts,
        end_time=end_ts,
        result=shooting_schedule_pb2.ShootingScheduleResult.SHOOTING_SCHEDULE_RESULT_PENDING_START,
        created_time=now_ts,
        updated_time=now_ts,
        state=shooting_schedule_pb2.ShootingScheduleState.SHOOTING_SCHEDULE_STATE_PENDING_SHOOT,
        param_mode=0,
        param_version=1,
        params=main_params,
        schedule_time=now_ts,
        sync_state=shooting_schedule_pb2.ShootingScheduleSyncState.SHOOTING_SCHEDULE_SYNC_STATE_PENDING_SYNC,
    )

    task_msg = sched_msg.shooting_tasks.add()
    task_msg.schedule_id = sched_id
    task_msg.schedule_task_id = task_id
    task_msg.params = json.dumps(task_params)
    task_msg.state = shooting_schedule_pb2.ShootingTaskState.SHOOTING_TASK_STATUS_IDLE
    task_msg.param_mode = 0
    task_msg.param_version = 1
    task_msg.create_from = 1
    task_msg.created_time = now_ts
    task_msg.updated_time = now_ts

    req_sync = shooting_schedule_pb2.ReqSyncShootingSchedule(shooting_schedule=sched_msg)

    resp_sync = await session._send_request(
        protocol_pb2.ModuleId.MODULE_SHOOTING_SCHEDULE,
        16100,
        req_sync,
        shooting_schedule_pb2.ResSyncShootingSchedule,
        timeout=8.0,
    )

    if resp_sync.code == 0:
        print(f"✅ ¡Plan '{args.name}' inyectado con éxito en el DWARF Mini!")
        print(f"   • Objetivo: {args.target} (RA: {args.ra}, Dec: {args.dec})")
        print(f"   • Horario:  {dt_start.strftime('%H:%M')} -> {dt_end.strftime('%H:%M')}")
        print(f"   • Ajustes:  {args.exp}s, Ganancia {args.gain}, Filtro {args.filter}")
        print(f"   • ID Plan:  {sched_id}")
    else:
        print(f"❌ Error al inyectar plan (código {resp_sync.code})")

    await session._ws_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

