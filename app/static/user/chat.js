const token = localStorage.getItem("user_token");
const userJson = localStorage.getItem("user_info");

if (!token) {
  window.location.href = "login.html";
}

const user = userJson ? JSON.parse(userJson) : null;
if (user) {
  document.getElementById("user-info").textContent = `${user.name}`;
}

document.getElementById("logout-link").addEventListener("click", () => {
  localStorage.removeItem("user_token");
  localStorage.removeItem("user_info");
  window.location.href = "login.html";
});

function authHeaders(extra = {}) {
  return { Authorization: `Bearer ${token}`, ...extra };
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { ...(options.headers || {}), ...authHeaders() },
  });
  if (res.status === 401) {
    localStorage.removeItem("user_token");
    localStorage.removeItem("user_info");
    window.location.href = "login.html";
    throw new Error("Session expired");
  }
  return res;
}

const messagesArea = document.getElementById("messages-area");
const composerForm = document.getElementById("composer-form");
const messageInput = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function renderMessages(messages) {
  messagesArea.innerHTML = "";

  if (!messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-chat";
    empty.id = "empty-chat";
    empty.textContent = "Ask a question to get started.";
    messagesArea.appendChild(empty);
    return;
  }

  messages.forEach((m) => messagesArea.appendChild(renderMessageRow(m)));
  scrollToBottom();
}

function renderMessageRow(m) {
  const row = document.createElement("div");
  row.className = `message-row ${m.sender === "customer" ? "from-user" : "from-agent"}`;
  row.dataset.messageId = m.id;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = m.body;
  row.appendChild(bubble);

  if (m.citations && m.citations.length) {
    const citeWrap = document.createElement("div");
    m.citations.forEach((c) => {
      const tag = document.createElement("span");
      tag.className = "citation-tag";
      tag.textContent = c.source;
      citeWrap.appendChild(tag);
    });
    row.appendChild(citeWrap);
  }

  const meta = document.createElement("div");
  meta.className = "msg-meta";

  const time = document.createElement("span");
  time.textContent = formatTime(m.timestamp);
  meta.appendChild(time);

  if (m.sender === "agent") {
    const fbWrap = document.createElement("span");
    fbWrap.className = "feedback-buttons";

    const upBtn = document.createElement("button");
    upBtn.className = `feedback-btn ${m.feedback === "good" ? "active-good" : ""}`;
    upBtn.textContent = "👍";
    upBtn.title = "Good response";
    upBtn.addEventListener("click", () => sendFeedback(m.id, "good", upBtn, downBtn));

    const downBtn = document.createElement("button");
    downBtn.className = `feedback-btn ${m.feedback === "bad" ? "active-bad" : ""}`;
    downBtn.textContent = "👎";
    downBtn.title = "Bad response";
    downBtn.addEventListener("click", () => sendFeedback(m.id, "bad", upBtn, downBtn));

    fbWrap.appendChild(upBtn);
    fbWrap.appendChild(downBtn);
    meta.appendChild(fbWrap);
  }

  row.appendChild(meta);
  return row;
}

async function sendFeedback(messageId, value, upBtn, downBtn) {
  upBtn.classList.remove("active-good");
  downBtn.classList.remove("active-bad");
  if (value === "good") upBtn.classList.add("active-good");
  else downBtn.classList.add("active-bad");

  try {
    await apiFetch(`/chat/messages/${messageId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feedback: value }),
    });
  } catch (err) {
    // silently ignore - the UI already reflects the intended state
  }
}

function scrollToBottom() {
  messagesArea.scrollTop = messagesArea.scrollHeight;
}

function showTypingIndicator() {
  const el = document.createElement("div");
  el.className = "typing-indicator";
  el.id = "typing-indicator";
  el.textContent = "Support is typing...";
  messagesArea.appendChild(el);
  scrollToBottom();
}

function hideTypingIndicator() {
  const el = document.getElementById("typing-indicator");
  if (el) el.remove();
}

async function loadHistory() {
  const res = await apiFetch("/chat/me");
  const data = await res.json();
  renderMessages(data.messages || []);
}

composerForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;

  messageInput.value = "";
  sendBtn.disabled = true;

  // optimistic render of the user's own message
  const emptyEl = document.getElementById("empty-chat");
  if (emptyEl) emptyEl.remove();
  const tempRow = renderMessageRow({
    id: `temp-${Date.now()}`,
    sender: "customer",
    body: text,
    timestamp: new Date().toISOString(),
  });
  messagesArea.appendChild(tempRow);
  showTypingIndicator();
  scrollToBottom();

  try {
    const res = await apiFetch("/chat/me", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    const data = await res.json();
    hideTypingIndicator();

    if (!res.ok) {
      alert(data.detail || "Something went wrong sending that message.");
      return;
    }

    // re-fetch full history so IDs/timestamps/feedback state are all correct
    await loadHistory();
  } catch (err) {
    hideTypingIndicator();
    if (err.message !== "Session expired") {
      alert("Could not reach the server.");
    }
  } finally {
    sendBtn.disabled = false;
    messageInput.focus();
  }
});

loadHistory();
