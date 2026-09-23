#!/usr/bin/env python3
"""Script de sesión astronómica completa para DWARF Mini.

Permite configurar mediante JSON:
- Calibración automática (Plate Solving de estrellas)
- GOTO a coordenadas (RA / Dec)
- Selección de filtro (Astro / Duo-Band)
- Secuencia de capturas (tiempo de exposición, ganancia, número de tomas)
- Descarga continua de archivos FITS en tiempo real
- Live Stacking en directo en pantalla (con alineación de estrellas por ORB)
- Generación de script de procesado para Siril
"""

import argparse
import asyncio
import ftplib
import json
import os
import re
import sys
import time
import cv2
import numpy as np

from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.device_profile import get_device_profile
from dwarf_alpaca.dwarf.session import configure_session, get_session, shutdown_session

sys.stdout.reconfigure(line_buffering=True)


def parse_ra(ra_val) -> float:
    """Convierte RA a horas en formato float."""
    if isinstance(ra_val, (int, float)):
        return float(ra_val)
    s = str(ra_val).strip()
    match = re.match(r"(\d+)[h:\s]+(\d+)[m:\s]+([\d.]+)", s)
    if match:
        h, m, sec = match.groups()
        return float(h) + float(m) / 60.0 + float(sec) / 3600.0
    return float(s)


def parse_dec(dec_val) -> float:
    """Convierte Dec a grados en formato float."""
    if isinstance(dec_val, (int, float)):
        return float(dec_val)
    s = str(dec_val).strip()
    sign = -1.0 if "-" in s else 1.0
    clean = s.replace("-", "").replace("+", "")
    match = re.match(r"(\d+)[d:°\s]+(\d+)['m:\s]+([\d.]+)", clean)
    if match:
        d, m, sec = match.groups()
        return sign * (float(d) + float(m) / 60.0 + float(sec) / 3600.0)
    return float(s)


def read_fits_raw(fits_path: str) -> np.ndarray | None:
    """Lee un FITS bruto de 16 bits y devuelve el array float32 2D."""
    try:
        with open(fits_path, "rb") as f:
            content = f.read()
        end_idx = content.find(b"END ")
        if end_idx == -1:
            header_size = 2880
        else:
            header_size = ((end_idx // 2880) + 1) * 2880

        raw_i2 = np.frombuffer(content[header_size:], dtype=">i2")
        data = raw_i2.astype(np.float32) + 32768.0

        if len(data) == 720 * 1280:
            return data.reshape((720, 1280))
        elif len(data) == 1080 * 1920:
            return data.reshape((1080, 1920))
        else:
            # Dimensiones variables
            total = len(data)
            if total % 1280 == 0:
                return data.reshape((total // 1280, 1280))
            if total % 1920 == 0:
                return data.reshape((total // 1920, 1920))
            return None
    except Exception as e:
        print(f"Error al leer FITS {fits_path}: {e}")
        return None


def debayer_fits(img_2d: np.ndarray) -> np.ndarray:
    """Convierte array RAW Bayer a BGR 8 bits con balance de fondo y estirado automático."""
    norm16 = np.clip(img_2d * (65535.0 / 4095.0), 0, 65535).astype(np.uint16)
    bgr16 = cv2.cvtColor(norm16, cv2.COLOR_BayerBG2BGR)
    bgr_stretched = np.zeros_like(bgr16, dtype=np.uint8)
    for c in range(3):
        ch = bgr16[:, :, c].astype(np.float32)
        bg = np.median(ch)
        mad = np.median(np.abs(ch - bg))
        low = max(0.0, bg - mad)
        high = min(65535.0, bg + 16.0 * mad + 1.0)
        bgr_stretched[:, :, c] = np.clip((ch - low) / (high - low) * 255.0, 0, 255).astype(np.uint8)
    return bgr_stretched


def align_stars(img: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Alinea el nuevo fotograma con la imagen de referencia mediante detección de estrellas."""
    try:
        gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray_ref = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
        orb = cv2.ORB_create(400)
        kp1, des1 = orb.detectAndCompute(gray_img, None)
        kp2, des2 = orb.detectAndCompute(gray_ref, None)
        if des1 is None or des2 is None or len(kp1) < 8 or len(kp2) < 8:
            return img

        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = bf.match(des1, des2)
        if len(matches) < 8:
            return img

        matches = sorted(matches, key=lambda x: x.distance)[:40]
        pts1 = np.float32([kp1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
        pts2 = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)

        matrix, inliers = cv2.estimateAffinePartial2D(pts1, pts2)
        if matrix is None:
            return img

        h, w = ref.shape[:2]
        return cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    except Exception:
        return img


def write_siril_script(session_dir: str, target: str):
    """Genera un archivo de guión oficial de Siril para apilar la sesión."""
    script_path = os.path.join(session_dir, "apilar_con_siril.ssf")
    ssf_content = f"""# Script para Siril - Sesión DWARF Mini ({target})
# Ejecutar desde Siril: File -> Open Script -> seleccionar este archivo
requires 1.2.0

# 1. Directorio de trabajo
cd fits

# 2. Conversión de archivos FITS a secuencia de Siril
convert raw -out=../process -debayer

# 3. Registro y alineación de estrellas
cd ../process
register raw

# 4. Apilado con rechazo de píxeles aberrantes (Winsorized)
stack r_raw rej 3 3 -norm=addscale -out=../{target}_apilado_siril

# 5. Volver al directorio raíz
cd ..
close
"""
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(ssf_content)
    print(f"📜 Script de Siril creado en: {script_path}")


async def main():
    parser = argparse.ArgumentParser(description="Sesión Astronómica DWARF Mini")
    parser.add_argument("--config", default="sequence.json", help="Ruta al archivo JSON de secuencia")
    parser.add_argument("--ip", default="192.168.1.104", help="IP del DWARF Mini")
    parser.add_argument("--skip-calibration", action="store_true", help="Saltarse la calibración previa (GOTO directo)")
    parser.add_argument("--calibrate", action="store_true", help="Forzar la calibración previa antes del GOTO")
    parser.add_argument("--skip-goto", action="store_true", help="Saltarse el GOTO (disparar donde esté apuntando)")
    parser.add_argument("--resolution", choices=["1080p", "720p"], default=None, help="Resolución de captura: 1080p (sensor nativo completo) o 720p (recorte central)")
    args = parser.parse_args()

    # Cargar JSON
    if not os.path.exists(args.config):
        print(f"❌ Error: No se encuentra el archivo de configuración {args.config}")
        return 1

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    target_name = cfg.get("target_name", "Target")
    ra_hours = parse_ra(cfg.get("ra", 0.0))
    dec_degrees = parse_dec(cfg.get("dec", 0.0))
    exposure_sec = float(cfg.get("exposure_seconds", 10.0))
    gain = int(cfg.get("gain", 60))
    filter_name = cfg.get("filter", "Astro")
    total_frames = int(cfg.get("frame_count", 20))
    res_choice = (args.resolution or cfg.get("resolution", "1080p")).lower()
    mini_shooting_mode = 8 if res_choice in ("720", "720p") else 2

    # Control de calibración: CLI tiene prioridad sobre JSON
    if args.skip_calibration:
        auto_calibrate = False
    elif args.calibrate:
        auto_calibrate = True
    else:
        auto_calibrate = bool(cfg.get("auto_calibrate", True))

    skip_goto = args.skip_goto or bool(cfg.get("skip_goto", False))
    do_live_stack = bool(cfg.get("live_stack", True))
    show_window = bool(cfg.get("show_window", True))
    base_out = cfg.get("output_dir", "captures")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    session_dir = os.path.join(base_out, f"{target_name}_{timestamp}")
    fits_dir = os.path.join(session_dir, "fits")
    os.makedirs(fits_dir, exist_ok=True)

    print("==========================================================")
    print(f" 🔭 INICIANDO SESIÓN ASTRONÓMICA: {target_name}")
    print("==========================================================")
    print(f"  • Coordenadas:      RA = {ra_hours:.4f}h ({cfg.get('ra')}) | Dec = {dec_degrees:.4f}° ({cfg.get('dec')})")
    print(f"  • Exposición:       {exposure_sec} segundos por toma")
    print(f"  • Ganancia (Gain):  {gain}")
    print(f"  • Filtro óptico:    {filter_name}")
    print(f"  • Resolución:       {'1080p (Nativo 1920x1080, FOV completo)' if mini_shooting_mode == 2 else '720p (Recorte 1280x720)'}")
    print(f"  • Total tomas:      {total_frames} fotogramas")
    print(f"  • Auto-calibración: {'Activada' if auto_calibrate else 'Desactivada'}")
    print(f"  • Modo GOTO:        {'Saltar (disparar posición actual)' if skip_goto else 'GOTO hacia ' + target_name}")
    print(f"  • Carpeta destino:  {session_dir}")
    print("==========================================================\n")

    # Guardar copia del JSON en la carpeta de la sesión
    with open(os.path.join(session_dir, "config_utilizada.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    write_siril_script(session_dir, target_name)

    profile = get_device_profile("dwarfmini")
    lat = cfg.get("site_latitude") or cfg.get("latitude")
    lon = cfg.get("site_longitude") or cfg.get("longitude")
    settings = Settings(
        dwarf_ap_ip=args.ip,
        dwarf_device_model="dwarfmini",
        dwarf_ws_client_id=profile.ws_client_id,
        network_mode="sta",
        auto_calibrate_on_slew=auto_calibrate,
        allow_continue_without_darks=True,
        dwarf_mini_capture_mode="astro",
        dwarf_mini_shooting_mode=mini_shooting_mode,
        site_latitude=float(lat) if lat is not None else None,
        site_longitude=float(lon) if lon is not None else None,
    )

    configure_session(settings)
    session = await get_session()
    await session.acquire("telescope")
    await session.acquire("camera")

    try:
        # 1. Configuración de Filtro
        filter_pos = 1 if filter_name.lower() in ("duo-band", "duoband") else 0
        print(f"[1/4] 🔘 Ajustando filtro óptico a {filter_name} (posición {filter_pos})...")
        await session.set_filter_position(filter_pos)

        # 2. Calibración y GOTO
        if skip_goto:
            print(f"[2/4] ⏩ Saltando GOTO: disparando en la orientación y seguimiento actual del telescopio.")
        else:
            if auto_calibrate:
                print(f"[2/4] 🚀 Calibrando y lanzando GOTO hacia {target_name}...")
                print("     (El telescopio realizará autofoco y calibración de estrellas antes del GOTO)")
            else:
                print(f"[2/4] 🚀 Lanzando GOTO directo hacia {target_name} (sin recalibrar)...")
                print("     (Usando la calibración y alineación previamente establecida en el telescopio)")
            await session.telescope_slew_to_coordinates(
                ra_hours=ra_hours,
                dec_degrees=dec_degrees,
                target_name=target_name,
            )
            await session.wait_for_goto_completion()
            print("     ✅ Telescopio apuntado y siguiendo el objetivo con seguimiento sideral.")

        # 3. Configuración de Cámara
        print(f"\n[3/4] 📸 Configurando cámara astronómica: {exposure_sec}s, Gain={gain}, {total_frames} tomas...")
        await session.camera_connect()
        state = session.camera_state
        state.duration = exposure_sec
        state.requested_gain = gain
        state.filter_name = filter_name
        state.filter_index = filter_pos
        state.requested_bin = (1, 1)
        state.requested_frame_count = total_frames

        # Establecer baseline de carpetas remotas antes del disparo
        baseline_dirs = set()
        try:
            ftp_init = ftplib.FTP(args.ip, timeout=5)
            ftp_init.login()
            baseline_dirs = set(ftp_init.nlst("/Astronomy"))
            ftp_init.quit()
        except Exception as e:
            print(f"     (Aviso FTP baseline: {e})")

        # Disparar secuencia astronómica (live stacking workflow)
        await session.camera_start_exposure(exposure_sec, light=True, continue_without_darks=True)
        print("     ✅ Secuencia iniciada en el hardware del telescopio.")

        # 4. Monitorización, Descarga de FITS y Live Stacking
        print(f"\n[4/4] 🔄 Monitorizando capturas y realizando Live Stacking...")
        downloaded_files = set()
        downloaded_clean_names = set()
        stack_accumulator = None
        ref_color_frame = None
        stacked_count = 0

        remote_dir = None
        ftp = None

        while stacked_count < total_frames:
            await asyncio.sleep(2.0)

            # Conectar FTP si no está abierto
            if ftp is None:
                try:
                    ftp = ftplib.FTP(args.ip, timeout=5)
                    ftp.login()
                except Exception:
                    ftp = None
                    continue

            # Localizar la carpeta de la sesión en /Astronomy
            if remote_dir is None:
                try:
                    current_dirs = ftp.nlst("/Astronomy")
                    new_dirs = [d for d in current_dirs if d not in baseline_dirs and "DWARF_RAW_TELE" in d]
                    if new_dirs:
                        # Ordenar por fecha en el nombre
                        new_dirs.sort()
                        remote_dir = new_dirs[-1]
                        print(f"     📁 Carpeta remota detectada: {remote_dir}")
                    else:
                        # Si no hay nuevas, buscar por coincidencia de target reciente
                        target_matches = [d for d in current_dirs if f"DWARF_RAW_TELE_{target_name}" in d]
                        if target_matches:
                            target_matches.sort()
                            remote_dir = target_matches[-1]
                            print(f"     📁 Carpeta remota detectada por nombre: {remote_dir}")
                except Exception:
                    continue

            if not remote_dir:
                continue

            # Listar archivos FITS en la carpeta (separando tomas individuales de las apiladas por hardware)
            try:
                remote_files = ftp.nlst(remote_dir)
                fits_files = [f for f in remote_files if f.endswith(".fits") and not os.path.basename(f).startswith("stacked")]
            except Exception:
                ftp = None
                continue

            for r_file in fits_files:
                fname = os.path.basename(r_file)
                clean_name = fname[len("failed_"):] if fname.startswith("failed_") else fname
                if clean_name in downloaded_clean_names:
                    continue

                local_fits_path = os.path.join(fits_dir, clean_name)
                try:
                    with open(local_fits_path, "wb") as f_out:
                        ftp.retrbinary(f"RETR {r_file}", f_out.write)
                    downloaded_clean_names.add(clean_name)
                    downloaded_files.add(fname)
                    file_size = os.path.getsize(local_fits_path)
                    print(f"  📥 [{len(downloaded_clean_names)}/{total_frames}] Descargado: {clean_name} ({file_size:,} bytes)")
                except Exception as e:
                    print(f"  ⚠️ Error al descargar {fname}: {e}")
                    continue

                # Procesar para Live Stacking
                raw_data = read_fits_raw(local_fits_path)
                if raw_data is not None:
                    color_frame = debayer_fits(raw_data)
                    if ref_color_frame is None:
                        ref_color_frame = color_frame
                        stack_accumulator = color_frame.astype(np.float32)
                        stacked_count = 1
                    else:
                        aligned = align_stars(color_frame, ref_color_frame)
                        stack_accumulator += aligned.astype(np.float32)
                        stacked_count += 1

                    # Generar imagen acumulada
                    live_avg = (stack_accumulator / stacked_count).astype(np.uint8)

                    # Guardar vista previa actualizada en disco
                    preview_path = os.path.join(session_dir, "live_stack.jpg")
                    cv2.imwrite(preview_path, live_avg)

                    # Mostrar en ventana si está activado
                    if show_window:
                        try:
                            display = live_avg.copy()
                            cv2.putText(
                                display,
                                f"{target_name} | Tomas: {stacked_count}/{total_frames} | Exp: {exposure_sec}s | Gain: {gain}",
                                (20, 40),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.7,
                                (0, 255, 255),
                                2,
                            )
                            cv2.imshow("DWARF Mini - Live Stacking", display)
                            key = cv2.waitKey(50)
                            if key in (27, ord("q")):
                                print("\n⏹️ Detenido por el usuario.")
                                break
                        except Exception:
                            pass

        # Descargar el FITS apilado y fotos generadas internamente por el telescopio si existen
        if ftp is not None and remote_dir is not None:
            try:
                remote_files = ftp.nlst(remote_dir)
                firmware_extras = [f for f in remote_files if os.path.basename(f).startswith("stacked") or f.endswith(".json")]
                for s_file in firmware_extras:
                    s_fname = os.path.basename(s_file)
                    dest_path = os.path.join(fits_dir, s_fname) if s_fname.endswith(".fits") else os.path.join(session_dir, s_fname)
                    with open(dest_path, "wb") as f_out:
                        ftp.retrbinary(f"RETR {s_file}", f_out.write)
                    print(f"  📥 [Firmware] Descargado: {s_fname}")
            except Exception as e:
                pass
            try:
                ftp.quit()
            except Exception:
                pass


        if show_window:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass

        print("\n==========================================================")
        print(" 🎉 ¡SESIÓN ASTRONÓMICA COMPLETADA CON ÉXITO!")
        print("==========================================================")
        print(f"  • Total tomas descargadas: {len(downloaded_files)}")
        print(f"  • Total tomas apiladas:    {stacked_count}")
        print(f"  • Vista previa apilada:    {os.path.join(session_dir, 'live_stack.jpg')}")
        print(f"  • Script para Siril:       {os.path.join(session_dir, 'apilar_con_siril.ssf')}")
        print("==========================================================\n")

    except KeyboardInterrupt:
        print("\n\n⚠️ Sesión cancelada por el usuario (Ctrl+C). Limpiando montura...")
    except Exception as exc:
        print(f"\n\n❌ Error durante la sesión: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            await session.camera_abort_exposure()
        except Exception:
            pass
        await session.release("camera")
        await session.release("telescope")
        await shutdown_session()
        print("Sesión cerrada limpiamente y telescopio liberado.")


if __name__ == "__main__":
    asyncio.run(main())
