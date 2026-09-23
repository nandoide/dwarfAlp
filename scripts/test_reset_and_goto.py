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
    ReqGetDeviceStateInfo,
    ResGetDeviceStateInfo,
)
from dwarf_alpaca.proto.base_pb2 import ComResponse
from dwarf_alpaca.proto.astro_pb2 import (
    ReqStopGoto,
    ReqStopCaptureRawLiveStacking,
    ReqStopCalibration,
    ReqStopTrackSpecialTarget,
    ReqStopOneClickGoto,
    ReqGotoDSO,
)

async def send_stop(session, cmd_id, req, name):
    try:
        res = await session._send_request(
            protocol_pb2.ModuleId.MODULE_ASTRO,
            cmd_id,
            req,
            ComResponse,
            timeout=3.0,
        )
        print(f"  {name} ({cmd_id}): code={getattr(res, 'code', 'OK')}")
    except Exception as e:
        print(f"  {name} ({cmd_id}) error: {e}")

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

    print("1. Sending all ASTRO stop commands to clear any active state...")
    await send_stop(session, protocol_pb2.DwarfCMD.CMD_ASTRO_STOP_CAPTURE_RAW_LIVE_STACKING, ReqStopCaptureRawLiveStacking(), "Stop Live Stacking")
    await send_stop(session, protocol_pb2.DwarfCMD.CMD_ASTRO_STOP_GOTO, ReqStopGoto(), "Stop Goto")
    await send_stop(session, protocol_pb2.DwarfCMD.CMD_ASTRO_STOP_TRACK_SPECIAL_TARGET, ReqStopTrackSpecialTarget(), "Stop Track Special")
    await send_stop(session, protocol_pb2.DwarfCMD.CMD_ASTRO_STOP_CALIBRATION, ReqStopCalibration(), "Stop Calibration")
    await send_stop(session, protocol_pb2.DwarfCMD.CMD_ASTRO_STOP_ONE_CLICK_GOTO, ReqStopOneClickGoto(), "Stop One Click Goto")

    print("\n2. Switching shooting mode to 8 (Deep Sky)...")
    try:
        await session._send_request(
            protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
            protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_MANAGER_SWITCH_SHOOTING_MODE,
            ReqSwitchShootingMode(mode=8),
            ResSwitchShootingMode,
            timeout=8.0,
        )
        print("  Mode 8 selected.")
    except Exception as e:
        print(f"  Mode 8 error: {e}")

    await asyncio.sleep(1.0)

    print("\n3. Testing ReqGotoDSO (11002) directly...")
    ra_hours = 13.0 + 42.0/60.0 + 7.0/3600.0
    dec_deg = 28.0 + 22.0/60.0 + 29.0/3600.0
    await session.telescope_slew_to_coordinates(
        ra_hours=ra_hours,
        dec_degrees=dec_deg,
        target_name="M3",
    )
    print("  Waiting for GOTO to slew, plate-solve, and engage tracking...")
    res, reason = await session.wait_for_goto_completion(timeout=60.0)
    print(f"  Result: {res}, Reason: {reason}")
    if res == "success":
        print("  🎉 GOTO COMPLETED & SIDERICAL TRACKING ACTIVE!")

    await session.release("camera")
    await session.release("telescope")

if __name__ == "__main__":
    asyncio.run(main())
