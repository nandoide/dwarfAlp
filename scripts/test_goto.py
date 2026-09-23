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

    print("Switching shooting mode to 8...")
    res_mode = await session._send_request(
        protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
        protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_MANAGER_SWITCH_SHOOTING_MODE,
        ReqSwitchShootingMode(mode=8),
        ResSwitchShootingMode,
        timeout=8.0,
    )
    print(f"Mode 8 response: {res_mode}")

    print("Entering camera...")
    res_cam = await session._send_request(
        protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
        protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_MANAGER_ENTER_CAMERA,
        ReqEnterCamera(),
        ResEnterCamera,
        timeout=8.0,
    )
    print(f"Enter camera response: {res_cam}")

    ra_hours = 13.0 + 42.0/60.0 + 7.0/3600.0
    dec_deg = 28.0 + 22.0/60.0 + 29.0/3600.0
    print(f"\nLaunching direct GOTO to M3: RA={ra_hours:.4f}h ({ra_hours*15:.4f}°), Dec={dec_deg:.4f}°...")
    t0 = time.time()
    await session.telescope_slew_to_coordinates(
        ra_hours=ra_hours,
        dec_degrees=dec_deg,
        target_name="M3",
    )
    print("Waiting for GOTO to complete and tracking to start...")
    res, reason = await session.wait_for_goto_completion(timeout=60.0)
    print(f"GOTO result: {res}, reason: {reason} (took {time.time()-t0:.1f}s)")

    await session.release("camera")
    await session.release("telescope")

if __name__ == "__main__":
    asyncio.run(main())
