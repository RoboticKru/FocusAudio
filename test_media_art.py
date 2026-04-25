import asyncio
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
from winsdk.windows.storage.streams import Buffer, InputStreamOptions

async def get_media_info():
    manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
    session = manager.get_current_session()
    if not session:
        print("No active media session")
        return
        
    info = await session.try_get_media_properties_async()
    print(f"Title: {info.title}")
    
    if info.thumbnail:
        print("Thumbnail found, attempting to read...")
        try:
            stream = await info.thumbnail.open_read_async()
            buffer = Buffer(stream.size)
            await stream.read_async(buffer, stream.size, InputStreamOptions.NONE)
            # Access underlying bytes using memoryview
            bytes_data = memoryview(buffer).tobytes()
            print(f"Successfully read {len(bytes_data)} bytes of thumbnail data.")
            with open("thumb_test.png", "wb") as f:
                f.write(bytes_data)
        except Exception as e:
            print(f"Error reading thumbnail: {e}")

if __name__ == "__main__":
    asyncio.run(get_media_info())
