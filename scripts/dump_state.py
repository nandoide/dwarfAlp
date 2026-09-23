import asyncio
from google.protobuf.json_format import MessageToJson
from dwarf_alpaca.config.settings import Settings
from dwarf_alpaca.dwarf.session import configure_session, get_session
from dwarf_alpaca.proto import protocol_pb2
from dwarf_alpaca.proto.task_center_pb2 import ReqGetDeviceStateInfo, ResGetDeviceStateInfo

async def main():
    settings = Settings(
        dwarf_ap_ip="192.168.1.104",
        dwarf_device_model="dwarfmini",
    )
    configure_session(settings)
    session = await get_session()
    await session.acquire("camera")

    res = await session._send_request(
        protocol_pb2.ModuleId.MODULE_DEVICE_CONFIG,
        protocol_pb2.DwarfCMD.CMD_GLOBAL_TASK_GET_DEVICE_STATE_INFO,
        ReqGetDeviceStateInfo(),
        ResGetDeviceStateInfo,
        timeout=5.0,
    )
    print(MessageToJson(res))
    await session.release("camera")

if __name__ == "__main__":
    asyncio.run(main())
