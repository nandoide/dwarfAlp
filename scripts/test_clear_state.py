import asyncio
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

async def check_state(session, label):
    res = await session._send_request(
        protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
        protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_GET_DEVICE_STATE_INFO,
        ReqGetDeviceStateInfo(),
        ResGetDeviceStateInfo,
        timeout=5.0,
    )
    print(f"\n--- {label} ---")
    print(f"shooting_mode: {res.shooting_mode}")
    print(f"tele exclusive_state: {res.tele_camera_state_info.exclusive_state}")
    return res

async def main():
    settings = Settings(
        dwarf_ap_ip="192.168.1.104",
        dwarf_device_model="dwarfmini",
    )
    configure_session(settings)
    session = await get_session()
    await session.acquire("camera")

    await check_state(session, "Initial State")

    print("\nSwitching to Mode 1 (Photo)...")
    await session._send_request(
        protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
        protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_MANAGER_SWITCH_SHOOTING_MODE,
        ReqSwitchShootingMode(mode=1),
        ResSwitchShootingMode,
        timeout=8.0,
    )
    await check_state(session, "After Mode 1")

    print("\nSwitching to Mode 8 (Deep Sky)...")
    await session._send_request(
        protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
        protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_MANAGER_SWITCH_SHOOTING_MODE,
        ReqSwitchShootingMode(mode=8),
        ResSwitchShootingMode,
        timeout=8.0,
    )
    await check_state(session, "After Mode 8")

    await session.release("camera")

if __name__ == "__main__":
    asyncio.run(main())
