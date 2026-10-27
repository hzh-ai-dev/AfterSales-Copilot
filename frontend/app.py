import os
from pathlib import Path
from uuid import uuid4

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
st.set_page_config(page_title="AfterSales Copilot", page_icon="💬", layout="wide")
st.title("AfterSales Copilot")
st.caption("多轮售后决策助手 · 所有政策、退款和换货操作均为本地模拟")

BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8011").rstrip("/")
ACCOUNTS = {
    "演示用户一": os.getenv("DEMO_USER_TOKEN", "demo-user-token"),
    "演示用户二": os.getenv("DEMO_OTHER_TOKEN", "demo-other-token"),
    "管理员": os.getenv("DEMO_ADMIN_TOKEN", "demo-admin-token"),
}


def api(method, path, payload=None):
    try:
        response = requests.request(
            method,
            BASE + path,
            json=payload,
            headers={"Authorization": "Bearer " + ACCOUNTS[account]},
            timeout=240,
        )
        if not response.ok:
            if method == "POST" and path.startswith("/chat/"):
                st.session_state.pop("pending_chat", None)
            detail = response.json().get("detail", "请求失败")
            if isinstance(detail, dict) and detail.get("session_id"):
                st.session_state.sid = detail["session_id"]
            st.error(str(detail))
            return None
        return response.json()
    except (requests.RequestException, ValueError):
        st.error("无法连接服务或服务响应异常，请确认后端已启动。")
        return None


with st.sidebar:
    account = st.selectbox("当前身份", list(ACCOUNTS))
    if st.session_state.get("account") != account:
        st.session_state.account = account
        st.session_state.sid = None
        st.session_state.pop("pending_chat", None)
    st.caption("仅供本地演示；正式系统需替换为真实登录机制。")
    if st.button("新建会话"):
        st.session_state.sid = None
        st.session_state.pop("pending_chat", None)
        st.rerun()
    st.button("刷新状态")
    sessions = api("GET", "/sessions")
    if sessions:
        selected = st.selectbox("历史会话", ["请选择"] + [s["id"] for s in sessions])
        if st.button("打开历史会话") and selected != "请选择":
            st.session_state.sid = selected
            st.rerun()
    st.markdown("**演示订单**")
    st.caption(
        "A1001：签收12天，有客服争议\n\nA1002：签收3天，普通售后\n\nA1003：超过保修期\n\nA2001：属于用户二"
    )

health = api("GET", "/health")
if health:
    st.info(
        "离线演示模式：使用确定性模拟模型，不会调用 DeepSeek。"
        if health["mode"] == "demo"
        else "DeepSeek 模式：使用真实模型 API，业务操作仍为模拟。"
    )

if account == "管理员":
    st.subheader("人工审核")
    reviews = api("GET", "/reviews/pending")
    if not reviews:
        st.info("暂无待审核申请。")
    for review in reviews or []:
        with st.expander("审核 " + review["id"], expanded=True):
            st.json(review["payload"])
            with st.form("review_" + review["id"]):
                choice = st.selectbox("审核决定", ["通过", "拒绝", "要求补充信息"])
                reason = st.text_input("审核理由 / 需要补充的问题")
                if st.form_submit_button("提交审核"):
                    if not reason.strip():
                        st.warning("请填写审核理由或补充问题。")
                    else:
                        result = api(
                            "POST",
                            "/reviews/" + review["id"] + "/decision",
                            {
                                "decision": {
                                    "通过": "approve",
                                    "拒绝": "reject",
                                    "要求补充信息": "request_info",
                                }[choice],
                                "reason": reason,
                            },
                        )
                        if result:
                            st.session_state.sid = result["session_id"]
                            st.rerun()

left, right = st.columns([3, 2])
sid = st.session_state.get("sid")
session = api("GET", "/session/" + sid) if sid else None

with left:
    st.subheader("售后对话")
    if not sid:
        st.write("例如：我的耳机只有一边有声音，我想换货。")
    if session:
        st.caption("会话编号：" + sid + " · 状态：" + session["status"])
        for message in session["messages"]:
            with st.chat_message(message["role"]):
                st.write(message["content"])
        if session["pause"]:
            with st.chat_message("assistant"):
                st.write(session["pause"]["question"])
        if session["status"] == "error":
            st.warning("执行失败，进度已保存。检查配置后可以重试。")
            if st.button("重试未完成操作"):
                if api("POST", "/session/" + sid + "/retry"):
                    st.rerun()
        if session["response_degraded"]:
            st.caption("回复生成失败，当前显示确定性的业务结果。")
    pending_chat = st.session_state.get("pending_chat")
    if pending_chat:
        st.warning("上一条请求的结果未确认。重试会使用原请求编号，避免重复创建申请。")
        if st.button("重试上一条请求"):
            result = api("POST", pending_chat["path"], pending_chat["payload"])
            if result:
                st.session_state.sid = result["session_id"]
                st.session_state.pop("pending_chat", None)
                st.rerun()
    can_chat = (
        account != "管理员"
        and not pending_chat
        and (not session or session["status"] == "waiting_user")
    )
    if can_chat:
        if message := st.chat_input("描述问题，或回答助手的追问"):
            with st.spinner("正在理解诉求并查询业务证据…"):
                payload = {"message": message, "request_id": str(uuid4())}
                if sid:
                    payload["session_id"] = sid
                path = "/chat/message" if sid else "/chat/start"
                st.session_state.pending_chat = {"path": path, "payload": payload}
                result = api("POST", path, payload)
                if result:
                    st.session_state.sid = result["session_id"]
                    st.session_state.pop("pending_chat", None)
                    st.rerun()
                if st.session_state.get("pending_chat") or st.session_state.get("sid"):
                    st.rerun()
    elif session and session["status"] == "completed":
        st.success("本次处理已结束。新的售后问题请新建会话。")

with right:
    st.subheader("Agent 执行状态")
    if session:
        st.metric("累计工具调用", session["tool_count"])
        st.write("当前节点：", "、".join(session["current_nodes"]) or "END")
        if session["proposed_solution"]:
            st.markdown("**规则判断与方案**")
            st.json(session["proposed_solution"])
        for evidence in session["evidence"]:
            with st.expander("证据 " + evidence["id"]):
                st.json(evidence["data"])
        with st.expander("节点与工具执行记录", expanded=False):
            for event in session["events"]:
                st.write(event["node"], event["detail"])
    else:
        st.write("开始对话后，将展示实际查询、证据和分支结果。")
