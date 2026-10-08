import json
import os
import re
import botpy
from botpy import logging
from botpy.message import GroupMessage

# ==================== 1. 配置参数与数据持久化 ====================

# 请在此处填入你的 QQ 开放平台开发者凭证
APP_ID = "1905746882"         # 例如: "102030405"
APP_SECRET = "Zn1GWm3LdwFZuFbyLj7WwMnFhAe8d8eB" # 例如: "abcdef1234567890xxxxxxxx"

# 数据保存的文件文件名
DATA_FILE = "bot_data.json"

def load_data():
    """从 JSON 文件中读取数据，不存在则自动初始化"""
    if not os.path.exists(DATA_FILE):
        default_data = {
            # ⚠️ 初始化管理员列表：请填入管理员的 OpenID/用户ID
            "admins": [
                "管理员OpenID_1"
            ],
            # 白名单用户列表 (OpenID/用户ID)
            "whitelist": [],
            # 评价数据格式：{"账号": [{"by": "录入者ID", "text": "评价内容"}, ...]}
            "reviews": {}
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=4)
        return default_data

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _log.error(f"读取数据文件失败，原因: {e}")
        return {"admins": [], "whitelist": [], "reviews": {}}

def save_data(data):
    """保存数据到 JSON 文件，确保重启不会丢失"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# ==================== 2. 机器人逻辑实现 ====================

_log = logging.get_logger()

class ReviewBot(botpy.Client):
    async def on_group_at_message_create(self, message: GroupMessage):
        """
        触发条件：仅在群聊中被 @机器人 时调用
        """
        data = load_data()
        
        # 获取发送消息用户的唯一标识 (OpenID)
        sender_id = message.author.member_openid  
        content = message.content.strip()

        # 校验当前用户的权限
        is_admin = sender_id in data.get("admins", [])
        is_whitelisted = (sender_id in data.get("whitelist", [])) or is_admin

        # 正则表达式匹配指令
        add_wl_match = re.search(r"加白\s+(.+)", content)
        del_wl_match = re.search(r"删白\s+(.+)", content)
        add_rev_match = re.search(r"录入\s+(\S+)\s+(.+)", content)
        query_rev_match = re.search(r"查询\s+(\S+)", content)
        del_rev_match = re.search(r"删评\s+(\S+)\s+(\d+)", content)

        # ---------------- 1. 管理员指令：添加白名单 ----------------
        if add_wl_match:
            if not is_admin:
                await message.reply(content="❌ 权限不足：仅管理员可以添加白名单。")
                return
            
            target_user = add_wl_match.group(1).strip()
            if target_user not in data["whitelist"]:
                data["whitelist"].append(target_user)
                save_data(data)
                await message.reply(content=f"✅ 已成功将用户 [{target_user}] 添加到白名单。")
            else:
                await message.reply(content=f"⚠️ 用户 [{target_user}] 已在白名单中。")
            return

        # ---------------- 2. 管理员指令：删除白名单 ----------------
        if del_wl_match:
            if not is_admin:
                await message.reply(content="❌ 权限不足：仅管理员可以删除白名单。")
                return
            
            target_user = del_wl_match.group(1).strip()
            if target_user in data["whitelist"]:
                data["whitelist"].remove(target_user)
                save_data(data)
                await message.reply(content=f"✅ 已成功将用户 [{target_user}] 移出白名单。")
            else:
                await message.reply(content=f"⚠️ 用户 [{target_user}] 不在白名单列表中。")
            return

        # ---------------- 权限拦截 ----------------
        # 非白名单/非管理员用户无法使用接下来的所有功能
        if not is_whitelisted:
            await message.reply(content="❌ 您不在白名单中，暂无使用权限。")
            return

        # ---------------- 3. 白名单 & 管理员：录入账号评价 ----------------
        if add_rev_match:
            account = add_rev_match.group(1)
            review_text = add_rev_match.group(2)

            if account not in data["reviews"]:
                data["reviews"][account] = []

            # 追加评价记录（支持相同账号多次录入）
            data["reviews"][account].append({
                "by": sender_id,
                "text": review_text
            })
            save_data(data)
            await message.reply(content=f"✅ 账号 [{account}] 的评价录入成功！")
            return

        # ---------------- 4. 白名单 & 管理员：查询账号评价 ----------------
        if query_rev_match:
            account = query_rev_match.group(1)
            reviews = data["reviews"].get(account, [])

            if not reviews:
                # 查不到时按需求精准返回
                await message.reply(content="无评价录入")
            else:
                reply_msg = f"📋 账号 [{account}] 的评价记录（共 {len(reviews)} 条）：\n"
                for idx, item in enumerate(reviews, 1):
                    reply_msg += f"{idx}. {item['text']}\n"
                await message.reply(content=reply_msg.strip())
            return

        # ---------------- 5. 仅管理员：删除某条评价 ----------------
        if del_rev_match:
            if not is_admin:
                await message.reply(content="❌ 权限不足：白名单用户无法删除评价，仅管理员可删除。")
                return

            account = del_rev_match.group(1)
            try:
                index = int(del_rev_match.group(2)) - 1  # 用户输入的序号转为数组索引
            except ValueError:
                await message.reply(content="❌ 输入格式错误，评价序号必须是数字。")
                return

            reviews = data["reviews"].get(account, [])
            
            if not reviews or index < 0 or index >= len(reviews):
                await message.reply(content=f"❌ 删除失败：找不到账号 [{account}] 的第 {index + 1} 条评价。")
            else:
                removed_item = reviews.pop(index)
                # 若账号下的评价全部被删光，清空该账号键值
                if not reviews:
                    del data["reviews"][account]
                save_data(data)
                await message.reply(content=f"✅ 已成功删除账号 [{account}] 的第 {index + 1} 条评价。")
            return


# ==================== 3. 程序入口 ====================

if __name__ == "__main__":
    # 配置机器人监听的事件意图（公域/私域群消息）
    intents = botpy.Intents(public_guild_messages=True)
    
    client = ReviewBot(intents=intents)
    
    # 启动机器人
    client.run(
        appid=APP_ID, 
        secret=APP_SECRET
    )
