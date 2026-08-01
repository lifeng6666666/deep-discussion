import asyncio
import traceback

async def test():
    try:
        import edge_tts
        comm = edge_tts.Communicate('测试 edge-tts 连接', 'zh-CN-XiaoxiaoNeural')
        await comm.save('static/audio/edge_test_out.mp3')
        print('SAVE_OK')
    except Exception as e:
        print('EXCEPTION during comm.save:')
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test())
