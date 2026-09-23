import asyncio
import time
from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.dwarf.session import configure_session, get_session
from dwarf_alpaca.proto import protocol_pb2
from dwarf_alpaca.proto.astro_pb2 import (
    ReqStopGoto,
    ReqStopCaptureRawLiveStacking,
    ReqStopOneClickGoto,
    ReqOneClickGotoDSO,
    ResOneClickGoto,
)
from dwarf_alpaca.proto.base_pb2 import ComResponse

async def main():
    settings = Settings(
        dwarf_ap_ip="192.168.1.104",
        dwarf_device_model="dwarfmini",
        auto_calibrate_on_slew=False,
        allow_continue_without_darks=True,
    )
    configure_session(settings)
    session = await get_session()
    await session.acquire("telescope")
    await session.acquire("camera")

    print("1. Clearing any residual states...")
    try:
        await session._send_request(
            protocol_pb2.ModuleId.MODULE_ASTRO,
            protocol_pb2.DwarfCMD.CMD_ASTRO_STOP_CAPTURE_RAW_LIVE_STACKING,
            ReqStopCaptureRawLiveStacking(),
            ComResponse,
            timeout=2.0,
        )
    except Exception:
        pass

    try:
        await session._send_request(
            protocol_pb2.ModuleId.MODULE_ASTRO,
            protocol_pb2.DwarfCMD.CMD_ASTRO_STOP_GOTO,
            ReqStopGoto(),
            ComResponse,
            timeout=2.0,
        )
    except Exception:
        pass

    try:
        await session._send_request(
            protocol_pb2.ModuleId.MODULE_ASTRO,
            protocol_pb2.DwarfCMD.CMD_ASTRO_STOP_ONE_CLICK_GOTO,
            ReqStopOneClickGoto(),
            ComResponse,
            timeout=2.0,
        )
    except Exception:
        pass

    print("2. Sending ReqOneClickGotoDSO with goto_only=True...")
    ra_hours = 13.0 + 42.0/60.0 + 7.0/3600.0
    dec_deg = 28.0 + 22.0/60.0 + 29.0/3600.0
    lat = 40.549778
    lon = -3.656592

    req = ReqOneClickGotoDSO(
        ra=ra_hours,
        dec=dec_deg,
        target_name="M3",
        lon=lon,
        lat=lat,
        shooting_mode=8,
        goto_only=True,
    )

    future = await session._begin_request(
        protocol_pb2.ModuleId.MODULE_ASTRO,
        protocol_pb2.DwarfCMD.CMD_ASTRO_START_ONE_CLICK_GOTO_DSO,
        req,
        ResOneClickGoto,
    )
    print("  Dispatched ReqOneClickGotoDSO.")

    # Wait up to 30s and print notifications
    t_end = time.time() + 30.0
    while time.time() < t_end:
        if future.done():
            res = future.result()
            print(f"  Future done! step={getattr(res, 'step', None)}, code={getattr(res, 'code', None)}, all_end={getattr(res, 'all_end', None)}")
            break
        await asyncio.sleep(1.0)

    await session.release("camera")
    await session.release("telescope")

if __name__ == "__main__":
    asyncio.run(main())
