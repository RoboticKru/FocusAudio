import asyncio
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

async def main():
    manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
    sessions = manager.get_sessions()
    for s in sessions:
        info = s.get_playback_info()
        print(f"App: {s.source_app_user_model_id}, State: {info.playback_status}")
        if s.source_app_user_model_id.lower() == "chrome":
            print("Pausing chrome...")
            await s.try_pause_async()

if __name__ == "__main__":
    asyncio.run(main())
