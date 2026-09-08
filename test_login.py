
import asyncio
from engine import FFGloryClient

async def run():
    client = FFGloryClient()
    await client.start()
    print("Logged in successfully:", client.is_logged_in)
    print("User profile:", client.user_profile)
    groups = await client.get_my_groups()
    print("Total groups on ffglory.pro:", len(groups))
    for g in groups[:3]:
        print(" - Group:", g.get("group_id"), "Clan:", g.get("clan_id"), "Status:", g.get("status"))
    await client.close()

asyncio.run(run())
