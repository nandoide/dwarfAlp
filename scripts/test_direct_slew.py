import asyncio
import time
from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.dwarf.session import configure_session, get_session
from dwarf_alpaca.proto import protocol_pb2
from dwarf_alpaca.proto.task_center_pb2 import (
    ReqSwitchShootingMode,
    ResSwitchShootingMode,
    ReqEnterCamera,
    ResEnterCamera,
)
from dwarf_alpaca.proto.camera_pb2 import ReqSetPreviewQuality
from dwarf_alpaca.proto.astro_pb2 import ReqStopGoto, ReqStopCaptureRawLiveStacking, ReqGotoDSO
from dwarf_alpaca.proto.base_pb2 import ComResponse

async def main():
    settings = Settings(
        dwarf_ap_ip="192.168.1.104",
        dwarf_device_model="dwarfmini",
        auto_calibrate_on_slew=False,
    )
    configure_session(settings)
    session = await get_session()
    await session.acquire("telescope")
    await session.acquire("camera")

    print("Step 1: Stopping any active goto or capture...")
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

    print("Step 2: Switching shooting mode to 8...")
    res_mode = await session._send_request(
        protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
        protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_MANAGER_SWITCH_SHOOTING_MODE,
        ReqSwitchShootingMode(mode=8),
        ResSwitchShootingMode,
        timeout=8.0,
    )
    print(f"  Mode 8 response: code={getattr(res_mode, 'code', 'OK')}")

    print("Step 3: Entering camera...")
    enter_req = ReqEnterCamera()
    enter_req.client_param.encode_type = 1
    res_cam = await session._send_request(
        protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
        protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_MANAGER_ENTER_CAMERA,
        enter_req,
        ResEnterCamera,
        timeout=8.0,
    )
    print(f"  Enter camera response: code={getattr(res_cam, 'code', 'OK')}")

    print("Step 4: Setting preview quality...")
    await session._send_and_check(
        protocol_pb2.ModuleId.MODULE_CAMERA_TELE,
        protocol_pb2.DwarfCMD.CMD_CAMERA_TELE_SET_PREVIEW_QUALITY,
        ReqSetPreviewQuality(level=1),
        timeout=5.0,
    )
    await session._send_and_check(
        protocol_pb2.ModuleId.MODULE_CAMERA_WIDE,
        protocol_pb2.DwarfCMD.CMD_CAMERA_WIDE_SET_PREVIEW_QUALITY,
        ReqSetPreviewQuality(level=1),
        timeout=5.0,
    )

    print("Step 5: Sending ReqGotoDSO (11002)...")
    ra_hours = 13.0 + 42.0/60.0 + 7.0/3600.0
    dec_deg = 28.0 + 22.0/60.0 + 29.0/3600.0
    req = ReqGotoDSO(
        ra=ra_hours * 15.0,
        dec=dec_deg,
        target_name="M3",
        goto_only=False,
    )
    res_goto = await session._send_request(
        protocol_pb2.ModuleId.MODULE_ASTRO,
        protocol_pb2.DwarfCMD.CMD_ASTRO_START_GOTO_DSO,
        req,
        ComResponse,
        timeout=5.0,
    )
    print(f"  Goto 11002 response: code={getattr(res_goto, 'code', 'OK')}")

    await session.release("camera")
    await session.release("telescope")

if __name__ == "__main__":
    asyncio.run(main())
