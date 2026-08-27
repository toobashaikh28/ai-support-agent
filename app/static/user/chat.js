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

    const speakBtn = document.createElement("button");
    speakBtn.className = "speaker-btn";
    speakBtn.textContent = "🔊";
    speakBtn.title = "Read this reply aloud";
    speakBtn.addEventListener("click", () => speakText(m.body, speakBtn));

    fbWrap.appendChild(upBtn);
    fbWrap.appendChild(downBtn);
    fbWrap.appendChild(speakBtn);
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

function showEscalationBanner(escalation) {
  const existing = document.getElementById("escalation-banner");
  if (existing) existing.remove();
  if (!escalation) return;

  const banner = document.createElement("div");
  banner.className = "escalation-banner";
  banner.id = "escalation-banner";
  banner.innerHTML = escalation.already_escalated
    ? `This conversation is already with a human agent — reference <b>${escalation.ticket_reference}</b>.`
    : `This conversation has been passed to a human agent — reference <b>${escalation.ticket_reference}</b>. Someone will follow up shortly.`;
  messagesArea.parentElement.insertBefore(banner, messagesArea.nextSibling);
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
  if (data.open_ticket_reference) {
    showEscalationBanner({ ticket_reference: data.open_ticket_reference, already_escalated: true });
  }
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
    showEscalationBanner(data.escalation);
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

// --------------------------------------------------------------------------
// Voice input: record -> upload to /chat/me/voice -> transcript is run
// through the exact same grounded-reply pipeline as typed messages.
// Voice output: read the reply aloud client-side (Web Speech API) - no
// audio file ever comes back from the server, so nothing new to host or
// clean up on disk.
// --------------------------------------------------------------------------

const micBtn = document.getElementById("mic-btn");
const voiceStatus = document.getElementById("voice-status");

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

function pickMimeType() {
  const candidates = ["audio/webm", "audio/ogg", "audio/mp4"];
  for (const type of candidates) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(type)) return type;
  }
  return ""; // let the browser choose its default
}

function setVoiceStatus(text) {
  voiceStatus.textContent = text;
}

async function startRecording() {
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    alert("Voice recording isn't supported in this browser. Try Chrome or Edge.");
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    alert("Microphone permission was denied — voice input needs mic access to work.");
    return;
  }

  const mimeType = pickMimeType();
  mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
  audioChunks = [];

  mediaRecorder.addEventListener("dataavailable", (e) => {
    if (e.data.size > 0) audioChunks.push(e.data);
  });

  mediaRecorder.addEventListener("stop", () => {
    stream.getTracks().forEach((track) => track.stop()); // release the mic
    const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
    sendVoiceMessage(blob, mediaRecorder.mimeType || "audio/webm");
  });

  mediaRecorder.start();
  isRecording = true;
  micBtn.classList.add("recording");
  micBtn.textContent = "⏹";
  setVoiceStatus("Recording... click again to stop.");
}

function stopRecording() {
  if (mediaRecorder && isRecording) {
    mediaRecorder.stop();
  }
  isRecording = false;
  micBtn.classList.remove("recording");
  micBtn.textContent = "🎤";
}

micBtn.addEventListener("click", () => {
  if (isRecording) {
    stopRecording();
  } else {
    startRecording();
  }
});

function extensionForMimeType(mimeType) {
  if (mimeType.includes("webm")) return "webm";
  if (mimeType.includes("ogg")) return "ogg";
  if (mimeType.includes("mp4")) return "m4a";
  if (mimeType.includes("wav")) return "wav";
  return "webm";
}

async function sendVoiceMessage(blob, mimeType) {
  setVoiceStatus("Transcribing your message...");
  micBtn.disabled = true;
  sendBtn.disabled = true;

  const emptyEl = document.getElementById("empty-chat");
  if (emptyEl) emptyEl.remove();
  showTypingIndicator();

  const formData = new FormData();
  formData.append("audio", blob, `voice.${extensionForMimeType(mimeType)}`);

  try {
    const res = await apiFetch("/chat/me/voice", {
      method: "POST",
      body: formData, // no Content-Type header set on purpose - the browser
                       // adds the correct multipart boundary itself
    });
    const data = await res.json();
    hideTypingIndicator();

    if (!res.ok) {
      setVoiceStatus("");
      alert(data.detail || "Couldn't process that voice message.");
      return;
    }

    setVoiceStatus(`Heard: "${data.transcript}"`);
    await loadHistory();
    showEscalationBanner(data.escalation);
    speakText(data.reply); // voice out, automatically, for the voice-in turn
  } catch (err) {
    hideTypingIndicator();
    setVoiceStatus("");
    if (err.message !== "Session expired") {
      alert("Could not reach the server.");
    }
  } finally {
    micBtn.disabled = false;
    sendBtn.disabled = false;
  }
}

function speakText(text, triggerBtn = null) {
  if (!window.speechSynthesis) {
    alert("Voice output isn't supported in this browser. Try Chrome or Edge.");
    return;
  }
  window.speechSynthesis.cancel(); // stop anything already playing first

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.0;

  if (triggerBtn) {
    triggerBtn.classList.add("speaking");
    utterance.addEventListener("end", () => triggerBtn.classList.remove("speaking"));
    utterance.addEventListener("error", () => triggerBtn.classList.remove("speaking"));
  }

  window.speechSynthesis.speak(utterance);
}
