import sqlite3
import math
import os
import json
from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import MessageEvent, Bot, Message, MessageSegment
from nonebot.params import ArgPlainText, CommandArg
from nonebot.permission import SUPERUSER

# == 配置与数据库逻辑 (保持不变) ==
CONFIG_FILE = "config.json"
DB_FILE = "bot_data.db"
PAGE_SIZE = 5

def load_config():
    if not os.path.exists(CONFIG_FILE):
        default = {"admins": [], "whitelist": []}
        with open(CONFIG_FILE, "w") as f:
            json.dump(default, f, indent=4)
        return default
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

def get_db_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def init_db():
    conn = get_db_conn()
    conn.execute('''CREATE TABLE IF NOT EXISTS reviews 
                   (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    target_id TEXT, 
                    comment TEXT, 
                    reviewer_id INTEGER,
                    reviewer_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

def get_paged_records(target_id, page=1):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM reviews WHERE target_id = ?', (target_id,))
    total_count = cursor.fetchone()[0]
    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
    offset = (page - 1) * PAGE_SIZE
    cursor.execute(
        'SELECT id, comment, created_at FROM reviews WHERE target_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
        (target_id, PAGE_SIZE, offset))
    records = cursor.fetchall()
    conn.close()
    return records, total_pages, total_count

# == 权限校验拦截器 ==
async def check_permission(event: MessageEvent) -> bool:
    user_id = int(event.user_id)
    config = load_config()
    admins = [int(a) for a in config.get("admins", [])]
    whitelist = [int(w) for w in config.get("whitelist", [])]
    return user_id in admins or user_id in whitelist

async def check_admin(event: MessageEvent) -> bool:
    user_id = int(event.user_id)
    config = load_config()
    admins = [int(a) for a in config.get("admins", [])]
    return user_id in admins

# == 业务处理器 ==

# 1. 录入信息 (替代 Telegram 的 NAV_INPUT 流程)
input_cmd = on_command("录入", rule=check_permission, priority=5)

@input_cmd.got("target_id", prompt="🎯 录入模式\n请发送要录入的用户 ID:")
async def process_target_id(target_id: str = ArgPlainText()):
    if not target_id.lstrip('-').isdigit():
        await input_cmd.reject("⚠️ 格式错误！请输入数字 ID (发送 '取消' 退出)")

@input_cmd.got("comment", prompt="📝 请输入评价内容:")
async def process_comment(event: MessageEvent, target_id: str = ArgPlainText(), comment: str = ArgPlainText()):
    if comment == "取消":
        await input_cmd.finish("已取消录入。")
        
    conn = get_db_conn()
    conn.execute(
        'INSERT INTO reviews (target_id, comment, reviewer_id, reviewer_name) VALUES (?, ?, ?, ?)',
        (target_id, comment, event.user_id, event.sender.nickname)
    )
    conn.commit()
    conn.close()
    await input_cmd.finish(f"✅ ID: {target_id} 的记录录入成功！")


# 2. 查询与翻页 (替代 Telegram 的 NAV_SEARCH 和 PAGE)
search_cmd = on_command("查询", rule=check_permission, priority=5)

@search_cmd.handle()
async def handle_search_args(args: Message = CommandArg()):
    # 允许直接使用 /查询 12345 2 (直接查第二页)
    args_text = args.extract_plain_text().strip().split()
    if args_text:
        search_cmd.set_arg("search_args", args)

@search_cmd.got("search_args", prompt="🔍 查询模式\n请发送要查询的 用户 ID (或带页码，如 '12345 2'):")
async def do_search(event: MessageEvent, search_args: str = ArgPlainText()):
    args = search_args.strip().split()
    target_id = args[0]
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1

    if not target_id.lstrip('-').isdigit():
        await search_cmd.reject("❌ 请输入有效的数字 ID。")

    records, total_pages, total_count = get_paged_records(target_id, page)
    
    if not records:
        await search_cmd.finish(f"❌ ID {target_id} 暂无记录或页码超出范围。")

    is_admin = await check_admin(event)
    
    msg = f"🔍 查询结果\nID: {target_id}\n总计: {total_count} 条\n第 {page}/{total_pages} 页\n===============\n"
    for i, (r_id, comment, dt) in enumerate(records, 1):
        idx = (page - 1) * PAGE_SIZE + i
        msg += f"[{idx}] 记录号:{r_id}\n{comment}\n📅 {dt}\n\n"
        
    msg += "===============\n"
    msg += f"👉 发送 /查询 {target_id} {page+1} 查看下一页"
    if is_admin:
        msg += "\n🗑️ 超管提示: 发送 /删除 记录号(非序号) 即可删除"

    await search_cmd.finish(msg)


# 3. 超管命令：删除
delete_cmd = on_command("删除", rule=check_admin, priority=5)

@delete_cmd.handle()
async def handle_delete(args: Message = CommandArg()):
    r_id = args.extract_plain_text().strip()
    if not r_id.isdigit():
        await delete_cmd.finish("用法: /删除 记录号 (纯数字)")
        
    conn = get_db_conn()
    conn.execute('DELETE FROM reviews WHERE id = ?', (r_id,))
    conn.commit()
    conn.close()
    await delete_cmd.finish(f"✅ 记录号 {r_id} 已成功删除。")


# 4. 超管命令：添加白名单
add_auth = on_command("add", rule=check_admin, priority=5)

@add_auth.handle()
async def handle_add(args: Message = CommandArg()):
    cfg = load_config()
    try:
        nid = int(args.extract_plain_text().strip())
        if nid not in cfg["whitelist"]:
            cfg["whitelist"].append(nid)
            save_config(cfg)
        await add_auth.finish(f"✅ 已授权白名单: {nid}")
    except ValueError:
        await add_auth.finish("用法: /add 用户ID (必须是纯数字)")


# 5. 超管命令：查看白名单
list_whitelist = on_command("list_whitelist", rule=check_admin, priority=5)

@list_whitelist.handle()
async def handle_list_whitelist():
    cfg = load_config()
    whitelist = cfg.get("whitelist", [])
    if not whitelist:
        await list_whitelist.finish("空空如也。")
    
    text = "📋 当前白名单用户：\n" + "\n".join([f"{i}. {uid}" for i, uid in enumerate(whitelist, 1)])
    await list_whitelist.finish(text)
