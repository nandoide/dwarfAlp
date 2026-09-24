#!/usr/bin/env python3
"""dwarf_schedule.py

Gestor de planificaciones astronómicas para DWARF Mini.
Permite listar, exportar e inyectar sesiones (mono y multi-objetivo, con soporte de mosaicos)
directamente en la memoria del telescopio mediante Protobuf sobre WebSockets.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
import time
from typing import Any

from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.dwarf.session import configure_session, get_session
from dwarf_alpaca.proto import protocol_pb2, shooting_schedule_pb2


# Mapeo oficial de tiempos de exposición del DWARF Mini a shutterIndex
SHUTTER_MAP: dict[int, int] = {
    1: 147,
    2: 148,
    5: 151,
    10: 154,
    15: 156,
    20: 157,
    25: 158,
    30: 159,
    45: 160,
    60: 162,
    90: 163,
    120: 165,
    180: 168,
}

# Inverso: shutterIndex -> segundos aproximados
SHUTTER_REV_MAP: dict[int, str] = {v: str(k) for k, v in SHUTTER_MAP.items()}


def format_ra(ra_hours: float) -> str:
    """Convierte horas decimales a formato HH:MM:SS."""
    h = int(ra_hours) % 24
    m_full = abs(ra_hours - int(ra_hours)) * 60.0
    m = int(m_full)
    s = (m_full - m) * 60.0
    return f"{h:02d}:{m:02d}:{s:04.1f}"


def format_dec(dec_degrees: float) -> str:
    """Convierte grados decimales a formato +DD:MM:SS o -DD:MM:SS."""
    sign = "-" if dec_degrees < 0 else "+"
    dec_abs = abs(dec_degrees)
    d = int(dec_abs)
    m_full = (dec_abs - d) * 60.0
    m = int(m_full)
    s = (m_full - m) * 60.0
    return f"{sign}{d:02d}:{m:02d}:{s:04.1f}"


def parse_ra(ra_val: Any) -> float:
    """Convierte 'HH:MM:SS', 'HHh MMm SSs' o número flotante a horas decimales."""
    if isinstance(ra_val, (int, float)):
        return float(ra_val)
    raw = str(ra_val).strip()
    import re
    nums = re.findall(r"[-+]?\d*\.?\d+", raw)
    if nums:
        h = float(nums[0])
        m = float(nums[1]) if len(nums) > 1 else 0.0
        s = float(nums[2]) if len(nums) > 2 else 0.0
        return h + m / 60.0 + s / 3600.0
    return 0.0


def parse_dec(dec_val: Any) -> float:
    """Convierte '+/-DD:MM:SS', '+DD° MM′ SS″' o número flotante a grados decimales."""
    if isinstance(dec_val, (int, float)):
        return float(dec_val)
    raw = str(dec_val).strip()
    sign = -1.0 if raw.startswith("-") else 1.0
    import re
    nums = re.findall(r"\d*\.?\d+", raw)
    if nums:
        d = float(nums[0])
        m = float(nums[1]) if len(nums) > 1 else 0.0
        s = float(nums[2]) if len(nums) > 2 else 0.0
        return sign * (d + m / 60.0 + s / 3600.0)
    return 0.0


def state_label(state_code: int) -> str:
    """Etiqueta legible del estado de un plan."""
    names = {
        0: "INITIALIZED",
        1: "PENDING_SHOOT (Pendiente)",
        2: "SHOOTING (Disparando)",
        3: "COMPLETED (Completado)",
        4: "EXPIRED (Expirado)",
    }
    return names.get(state_code, f"STATE_{state_code}")


def result_label(res_code: int) -> str:
    """Etiqueta legible del resultado de un plan."""
    names = {
        0: "PENDING",
        1: "SUCCESS (Completado)",
        2: "PARTIAL (Parcial)",
        3: "FAILED (Fallido)",
    }
    return names.get(res_code, f"RESULT_{res_code}")


async def get_dwarf_session(ip: str):
    """Establece sesión conectada al DWARF Mini."""
    settings = Settings(dwarf_ap_ip=ip, dwarf_device_model="dwarfmini")
    configure_session(settings)
    session = await get_session()
    await session._ensure_ws()
    return session


async def fetch_all_schedules(session) -> list[Any]:
    """Obtiene todos los planes almacenados en el DWARF Mini."""
    req = shooting_schedule_pb2.ReqGetAllShootingSchedule()
    resp = await session._send_request(
        protocol_pb2.ModuleId.MODULE_SHOOTING_SCHEDULE,
        16102,
        req,
        shooting_schedule_pb2.ResGetAllShootingSchedule,
        timeout=8.0,
    )
    if resp.code != 0:
        raise RuntimeError(f"Error al consultar planes del DWARF (código {resp.code})")
    return list(resp.shooting_schedule)


async def list_schedules(ip: str):
    """Muestra un resumen en terminal de los planes existentes en el telescopio."""
    print(f"\n📡 Conectando al DWARF Mini en {ip}...")
    session = await get_dwarf_session(ip)
    try:
        schedules = await fetch_all_schedules(session)
        print(f"📋 Planes encontrados en el telescopio: {len(schedules)}\n")
        print(f"{'#':<3} {'Nombre':<24} {'Estado':<24} {'Inicio (Local)':<18} {'Fin (Local)':<18} {'Tareas':<6}")
        print("-" * 98)

        # Ordenar: primero los pendientes (state 1, 0), luego por fecha
        sorted_s = sorted(schedules, key=lambda s: (s.state == 3, -s.start_time))

        for idx, s in enumerate(sorted_s, 1):
            t_start = datetime.datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d %H:%M") if s.start_time else "N/A"
            t_end = datetime.datetime.fromtimestamp(s.end_time).strftime("%Y-%m-%d %H:%M") if s.end_time else "N/A"
            lbl = state_label(s.state)
            print(f"{idx:<3} {s.schedule_name[:23]:<24} {lbl[:23]:<24} {t_start:<18} {t_end:<18} {len(s.shooting_tasks):<6}")
            for t_idx, t in enumerate(s.shooting_tasks, 1):
                try:
                    p = json.loads(t.params)
                    name = p.get("name") or "Desconocido"
                    ra_str = format_ra(p.get("ra", 0.0))
                    dec_str = format_dec(p.get("dec", 0.0))
                    filt = p.get("filterModeName", "Astro")
                    exp_s = p.get("shutterName", "?")
                    gain = p.get("gainName", "?")
                    mosaic_info = ""
                    is_wide = bool(p.get("cameraType") == 1 or p.get("camera_type") == 1 or p.get("cam_id") == 1 or p.get("cameraId") == 1)
                    if is_wide:
                        mosaic_info = " | [WIDE-ANGLE]"
                    elif p.get("isMosaicMode"):
                        h_sc = p.get("horizontalScale", 100)
                        v_sc = p.get("verticalScale", 100)
                        mosaic_info = f" | [MOSAICO {h_sc}%x{v_sc}%]"
                    print(f"     └─ T{t_idx}: {name} (RA: {ra_str}, Dec: {dec_str}) | {exp_s}s, Gain {gain}, {filt}{mosaic_info}")
                except Exception:
                    print(f"     └─ T{t_idx}: (Error al decodificar parámetros)")
        print()
    finally:
        await session._ws_client.close()


async def export_schedules(ip: str, output_path: str):
    """Exporta todos los planes del DWARF Mini a un archivo JSON estructurado."""
    print(f"\n📡 Conectando al DWARF Mini en {ip} para exportar...")
    session = await get_dwarf_session(ip)
    try:
        schedules = await fetch_all_schedules(session)
        print(f"📦 Procesando {len(schedules)} planes...")

        export_data: list[dict[str, Any]] = []

        # Ordenar por fecha de inicio descendente
        schedules.sort(key=lambda s: s.start_time, reverse=True)

        for s in schedules:
            dt_start = datetime.datetime.fromtimestamp(s.start_time) if s.start_time else None
            dt_end = datetime.datetime.fromtimestamp(s.end_time) if s.end_time else None

            # Deserializar parámetros generales del plan si existen
            gen_params = {}
            try:
                gen_params = json.loads(s.params) if s.params else {}
            except Exception:
                pass

            sched_dict: dict[str, Any] = {
                "schedule_name": s.schedule_name,
                "schedule_id": s.schedule_id,
                "state": state_label(s.state),
                "state_code": s.state,
                "result": result_label(s.result),
                "auto_calibrate": (gen_params.get("calibrationMode", 1) == 0),
                "auto_focus": (gen_params.get("focusMode", 1) == 0),
                "date": dt_start.strftime("%Y-%m-%d") if dt_start else "",
                "start_time_local": dt_start.strftime("%H:%M") if dt_start else "",
                "end_time_local": dt_end.strftime("%H:%M") if dt_end else "",
                "start_timestamp": s.start_time,
                "end_timestamp": s.end_time,
                "location": {
                    "city": gen_params.get("cityName", ""),
                    "latitude": gen_params.get("latitude", 0.0),
                    "longitude": gen_params.get("longitude", 0.0),
                },
                "tasks": [],
            }

            for t in s.shooting_tasks:
                try:
                    p = json.loads(t.params)
                    task_start = datetime.datetime.fromtimestamp(p.get("startTime", s.start_time))
                    task_end = datetime.datetime.fromtimestamp(p.get("endTime", s.end_time))
                    ra_hours = p.get("ra", 0.0)
                    dec_degrees = p.get("dec", 0.0)

                    task_dict: dict[str, Any] = {
                        "name": p.get("name", ""),
                        "ra": format_ra(ra_hours),
                        "ra_hours": ra_hours,
                        "dec": format_dec(dec_degrees),
                        "dec_degrees": dec_degrees,
                        "start": task_start.strftime("%H:%M"),
                        "end": task_end.strftime("%H:%M"),
                        "exp": int(p.get("shutterName") or 15),
                        "gain": int(p.get("gainName") or 60),
                        "filter": p.get("filterModeName", "Astro"),
                        "mosaic": {
                            "enabled": bool(p.get("isMosaicMode", False)),
                            "horizontal_scale": int(p.get("horizontalScale", 100)),
                            "vertical_scale": int(p.get("verticalScale", 100)),
                            "rotation": int(p.get("rotation", -1)),
                        },
                    }
                    sched_dict["tasks"].append(task_dict)
                except Exception as e:
                    sched_dict["tasks"].append({"raw_params": t.params, "error": str(e)})

            export_data.append(sched_dict)

        result_payload = {
            "exported_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "device": "DWARF Mini",
            "ip": ip,
            "total_schedules": len(export_data),
            "schedules": export_data,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result_payload, f, indent=2, ensure_ascii=False)

        print(f"✅ ¡Exportación completada con éxito!")
        print(f"   📁 Archivo generado: {os.path.abspath(output_path)}")
        print(f"   📊 Total planes exportados: {len(export_data)}")
    finally:
        await session._ws_client.close()


async def delete_schedule(ip: str, target: str):
    """Elimina un plan del DWARF Mini por ID o por nombre utilizando el comando 16108."""
    print(f"\n📡 Conectando al DWARF Mini en {ip} para eliminar plan...")
    session = await get_dwarf_session(ip)
    try:
        schedules = await fetch_all_schedules(session)
        target_sched = None

        # Intentar coincidencia por ID exacto
        for s in schedules:
            if s.schedule_id == target:
                target_sched = s
                break

        # Si no, buscar por nombre exacto o parcial
        if not target_sched:
            for s in schedules:
                if s.schedule_name.lower() == target.lower():
                    target_sched = s
                    break

        # Si es un número (índice 1-based del listado)
        if not target_sched and target.isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(schedules):
                target_sched = schedules[idx]

        if not target_sched:
            print(f"❌ No se encontró ningún plan con identificador, nombre o índice '{target}'.")
            return

        sched_id = target_sched.schedule_id
        sched_name = target_sched.schedule_name
        print(f"🗑️ Eliminando plan '{sched_name}' (ID: {sched_id})...")

        req = shooting_schedule_pb2.ReqDeleteShootingSchedule(id=sched_id, password="")
        resp = await session._send_request(
            protocol_pb2.ModuleId.MODULE_SHOOTING_SCHEDULE,
            16108,
            req,
            shooting_schedule_pb2.ResDeleteShootingSchedule,
            timeout=8.0,
        )

        if resp.code == 0:
            print(f"✅ ¡Plan '{sched_name}' ({sched_id}) eliminado con éxito del telescopio!")
        else:
            print(f"❌ Error al eliminar plan (código {resp.code}).")
    finally:
        await session._ws_client.close()


async def sync_plan(ip: str, plan_file: str):
    """Inyecta un plan desde archivo JSON en el DWARF Mini y verifica su guardado."""
    print(f"\n📖 Leyendo archivo de planificación: {plan_file}...")
    with open(plan_file, "r", encoding="utf-8") as f:
        content = json.load(f)

    # Admite formato con lista 'schedules' o formato simple de un solo plan
    schedules_to_inject = content.get("schedules") if "schedules" in content else [content]

    print(f"📡 Conectando al DWARF Mini en {ip}...")
    session = await get_dwarf_session(ip)
    try:
        # Sincronizar reloj del DWARF con la hora del sistema
        try:
            await session.sync_system_time()
            print("🕒 Reloj del DWARF Mini sincronizado con el Mac.")
        except Exception as e:
            print(f"⚠️ Aviso al sincronizar hora: {e}")

        # Obtener dispositivo base
        existing = await fetch_all_schedules(session)
        last_s = existing[0] if existing else None
        device_id = last_s.device_id if last_s else 4
        mac_address = last_s.mac_address if last_s else "40:FD:F3:1A:37:6C"
        base_city = "Alcobendas"
        base_lat = 40.55
        base_lon = -3.65
        if last_s and last_s.params:
            try:
                lp = json.loads(last_s.params)
                base_city = lp.get("cityName", base_city)
                base_lat = lp.get("latitude", base_lat)
                base_lon = lp.get("longitude", base_lon)
            except Exception:
                pass

        tz = datetime.datetime.now().astimezone().tzinfo

        for s_idx, s_data in enumerate(schedules_to_inject, 1):
            sched_name = s_data.get("schedule_name", f"Plan_{s_idx}")
            auto_calib = s_data.get("auto_calibrate", True)
            auto_focus = s_data.get("auto_focus", False)
            date_str = s_data.get("date")

            if date_str:
                base_date = datetime.date.fromisoformat(date_str)
            else:
                base_date = datetime.date.today()

            tasks_data = s_data.get("tasks", [])
            if not tasks_data:
                print(f"⚠️ Plan '{sched_name}' no tiene tareas. Omitiendo.")
                continue

            # Calcular timestamps de cada tarea
            parsed_tasks = []
            min_start = None
            max_end = None
            current_day = base_date

            for t in tasks_data:
                s_h, s_m = [int(p) for p in t.get("start", "21:30").split(":")]
                e_h, e_m = [int(p) for p in t.get("end", "23:00").split(":")]

                t_start = datetime.datetime(current_day.year, current_day.month, current_day.day, s_h, s_m, 0, tzinfo=tz)
                # Si t_start es anterior al fin de la tarea previa, hemos cruzado medianoche
                if max_end is not None and t_start.timestamp() < max_end:
                    current_day = current_day + datetime.timedelta(days=1)
                    t_start = datetime.datetime(current_day.year, current_day.month, current_day.day, s_h, s_m, 0, tzinfo=tz)

                t_end = datetime.datetime(current_day.year, current_day.month, current_day.day, e_h, e_m, 0, tzinfo=tz)
                # Si fin <= inicio, esta misma tarea cruza medianoche
                if t_end <= t_start:
                    current_day = current_day + datetime.timedelta(days=1)
                    t_end = datetime.datetime(current_day.year, current_day.month, current_day.day, e_h, e_m, 0, tzinfo=tz)

                s_ts = int(t_start.timestamp())
                e_ts = int(t_end.timestamp())

                if min_start is None or s_ts < min_start:
                    min_start = s_ts
                if max_end is None or e_ts > max_end:
                    max_end = e_ts

                # Validar y calcular mosaico
                mosaic_cfg = t.get("mosaic", {})
                is_mosaic = bool(mosaic_cfg.get("enabled", False))
                h_scale = int(mosaic_cfg.get("horizontal_scale", 100))
                v_scale = int(mosaic_cfg.get("vertical_scale", 100))
                rot = int(mosaic_cfg.get("rotation", -1))

                # Clamping de seguridad al límite de DWARFLAB (100 a 180)
                h_scale = max(100, min(180, h_scale))
                v_scale = max(100, min(180, v_scale))

                is_wide = bool(t.get("is_wide", False) or t.get("camera_type") == 1 or t.get("cam_id") == 1 or t.get("camera_id") == 1)
                exp_sec = int(t.get("exp", 15))
                shutter_idx = SHUTTER_MAP.get(exp_sec, 159)
                gain = int(t.get("gain", 60))
                filt_str = t.get("filter", "Astro")
                if is_wide:
                    filt_idx = 0
                    filt_name = "None"
                    camera_val = 1
                else:
                    filt_idx = 2 if filt_str.lower() in ("duo-band", "duoband") else 1
                    filt_name = "Duo-Band" if filt_idx == 2 else "Astro"
                    camera_val = 0

                ra_val = float(t["ra_hours"]) if "ra_hours" in t else parse_ra(t.get("ra", 0.0))
                dec_val = float(t["dec_degrees"]) if "dec_degrees" in t else parse_dec(t.get("dec", 0.0))

                parsed_tasks.append({
                    "name": t.get("name") or t.get("target") or "Objetivo",
                    "ra": ra_val,
                    "dec": dec_val,
                    "startTime": s_ts,
                    "endTime": e_ts,
                    "shutterIndex": shutter_idx,
                    "shutterName": str(exp_sec),
                    "gainIndex": gain,
                    "gainName": str(gain),
                    "filterModeIndex": filt_idx,
                    "filterModeName": filt_name,
                    "isMosaicMode": is_mosaic,
                    "horizontalScale": h_scale,
                    "verticalScale": v_scale,
                    "rotation": rot,
                    "stacked": 0,
                    "count": 0,
                    "atlasSortTypeValue": 0,
                    "cometQueryName": "",
                    "wellknownName": "",
                    "cameraType": camera_val,
                    "camera_type": camera_val,
                    "cameraId": camera_val,
                    "camera_id": camera_val,
                    "camId": camera_val,
                    "cam_id": camera_val,
                })

            # Construir mensaje de Schedule
            uid_prefix = "f26ca2d6-57c3-40d0-86fd-ee854e007474"
            now_msec = int(time.time() * 1000)
            sched_id = f"{uid_prefix}{now_msec}.MacOS"

            gen_params_dict = {
                "calibrationMode": 0 if auto_calib else 1,
                "focusMode": 0 if auto_focus else 1,
                "cityName": base_city,
                "latitude": base_lat,
                "longitude": base_lon,
            }

            sched_msg = shooting_schedule_pb2.ShootingScheduleMsg(
                schedule_id=sched_id,
                schedule_name=sched_name,
                device_id=device_id,
                mac_address=mac_address,
                start_time=min_start,
                end_time=max_end,
                result=shooting_schedule_pb2.ShootingScheduleResult.SHOOTING_SCHEDULE_RESULT_PENDING_START,
                created_time=int(now_msec / 1000),
                updated_time=int(now_msec / 1000),
                state=shooting_schedule_pb2.ShootingScheduleState.SHOOTING_SCHEDULE_STATE_PENDING_SHOOT,
                param_mode=0,
                param_version=1,
                params=json.dumps(gen_params_dict),
                schedule_time=int(now_msec / 1000),
                sync_state=shooting_schedule_pb2.ShootingScheduleSyncState.SHOOTING_SCHEDULE_SYNC_STATE_PENDING_SYNC,
            )

            for t_i, pt in enumerate(parsed_tasks, 1):
                task_id = f"{uid_prefix}{now_msec + t_i * 100}"
                task_msg = sched_msg.shooting_tasks.add()
                task_msg.schedule_id = sched_id
                task_msg.schedule_task_id = task_id
                task_msg.params = json.dumps(pt)
                task_msg.state = shooting_schedule_pb2.ShootingTaskState.SHOOTING_TASK_STATUS_IDLE
                task_msg.param_mode = 0
                task_msg.param_version = 1
                task_msg.create_from = 1
                task_msg.created_time = int(now_msec / 1000)
                task_msg.updated_time = int(now_msec / 1000)

            # Enviar comando 16100
            print(f"\n🚀 Inyectando plan '{sched_name}' con {len(parsed_tasks)} tarea(s)...")
            req_sync = shooting_schedule_pb2.ReqSyncShootingSchedule(shooting_schedule=sched_msg)
            resp_sync = await session._send_request(
                protocol_pb2.ModuleId.MODULE_SHOOTING_SCHEDULE,
                16100,
                req_sync,
                shooting_schedule_pb2.ResSyncShootingSchedule,
                timeout=8.0,
            )

            if resp_sync.code != 0:
                print(f"❌ Error al sincronizar plan '{sched_name}' (código {resp_sync.code})")
                continue

            print(f"✅ ¡Plan '{sched_name}' aceptado por el telescopio!")

            # Verificación inmediata con comando 16102
            print("🔍 Verificando registro en la base de datos del DWARF Mini...")
            all_s = await fetch_all_schedules(session)
            matched = next((x for x in all_s if x.schedule_id == sched_id or x.schedule_name == sched_name), None)

            if matched:
                print("==================================================")
                print(f"  PLAN VERIFICADO EN HARDWARE: {matched.schedule_name}")
                print("==================================================")
                print(f"  • ID en DWARF:   {matched.schedule_id}")
                print(f"  • Estado:        {state_label(matched.state)}")
                print(f"  • Inicio local:  {datetime.datetime.fromtimestamp(matched.start_time).strftime('%Y-%m-%d %H:%M')}")
                print(f"  • Fin local:     {datetime.datetime.fromtimestamp(matched.end_time).strftime('%Y-%m-%d %H:%M')}")
                print(f"  • Tareas en DB:  {len(matched.shooting_tasks)}")
                for i_t, t_obj in enumerate(matched.shooting_tasks, 1):
                    p_v = json.loads(t_obj.params)
                    mos = f" [MOSAICO {p_v.get('horizontalScale')}%x{p_v.get('verticalScale')}%]" if p_v.get('isMosaicMode') else ""
                    print(f"     [{i_t}] {p_v.get('name')} | RA: {format_ra(p_v.get('ra', 0))} Dec: {format_dec(p_v.get('dec', 0))} | {p_v.get('shutterName')}s, Gain {p_v.get('gainName')}, {p_v.get('filterModeName')}{mos}")
                print("==================================================")
            else:
                print(f"⚠️ El plan se envió pero no se localizó en la consulta posterior.")
    finally:
        await session._ws_client.close()


def main():
    parser = argparse.ArgumentParser(description="Gestor de Planificaciones para DWARF Mini (dwarf_schedule)")
    parser.add_argument("file", nargs="?", help="Ruta al archivo JSON de planificación para inyectar")
    parser.add_argument("--ip", default="192.168.1.104", help="IP del DWARF Mini (por defecto 192.168.1.104)")
    parser.add_argument("--list", action="store_true", help="Listar todos los planes guardados en el telescopio")
    parser.add_argument("--export", metavar="OUTPUT_JSON", help="Exportar todos los planes del telescopio a un archivo JSON")
    parser.add_argument("--delete", metavar="PLAN_NAME_OR_ID", help="Eliminar un plan del telescopio por nombre, ID o número de índice")
    args = parser.parse_args()

    if args.list:
        asyncio.run(list_schedules(args.ip))
    elif args.export:
        asyncio.run(export_schedules(args.ip, args.export))
    elif args.delete:
        asyncio.run(delete_schedule(args.ip, args.delete))
    elif args.file:
        asyncio.run(sync_plan(args.ip, args.file))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
