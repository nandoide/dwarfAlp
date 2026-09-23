import asyncio
import time
from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.dwarf.session import configure_session, get_session
from dwarf_alpaca.proto import protocol_pb2
from dwarf_alpaca.proto.astro_pb2 import ReqGotoDSO
from dwarf_alpaca.proto.base_pb2 import ComResponse
from dwarf_alpaca.proto.task_center_pb2 import ReqGetDeviceStateInfo, ResGetDeviceStateInfo

async def get_state(session):
    res = await session._send_request(
        protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
        protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_GET_DEVICE_STATE_INFO,
        ReqGetDeviceStateInfo(),
        ResGetDeviceStateInfo,
        timeout=5.0,
    )
    return res

async def main():
    settings = Settings(
        dwarf_ap_ip="192.168.1.104",
        dwarf_device_model="dwarfmini",
        auto_calibrate_on_slew=False,
    )
    configure_session(settings)
    session = await get_session()
    await session.acquire("telescope")

    s0 = await get_state(session)
    print(f"Initial calibration_result: {s0.device_state_info.calibration_result}")
    print(f"Initial shooting_mode: {s0.shooting_mode}")

    ra_hours = 13.0 + 42.0/60.0 + 7.0/3600.0
    dec_deg = 28.0 + 22.0/60.0 + 29.0/3600.0
    req = ReqGotoDSO(
        ra=ra_hours * 15.0,
        dec=dec_deg,
        target_name="M3",
        goto_only=False,
    )

    print(f"\nSending 11002 ReqGotoDSO (ra={req.ra:.4f}°, dec={req.dec:.4f}°)...")
    res = await session._send_request(
        protocol_pb2.ModuleId.MODULE_ASTRO,
        protocol_pb2.DwarfCMD.CMD_ASTRO_START_GOTO_DSO,
        req,
        ComResponse,
        timeout=5.0,
    )
    print(f"11002 direct response: code={getattr(res, 'code', None)}")

    print("\nPolling device state for 20 seconds...")
    for i in range(10):
        await asyncio.sleep(2.0)
        s = await get_state(session)
        print(f"[{i*2+2}s] motor_state={s.motion_motor_state_info.exclusive_state}, calib={s.device_state_info.calibration_result}")

    await session.release("telescope")

if __name__ == "__main__":
    asyncio.run(main())
