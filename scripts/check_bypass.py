import sys
sys.path.insert(0, 'backend')
import asyncio
from database import get_setting
async def c():
    val = await get_setting("bypass")
    print(f"bypass = {repr(val)}")
asyncio.run(c())
