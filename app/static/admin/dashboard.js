const token = localStorage.getItem("admin_token");
const userJson = localStorage.getItem("admin_user");

if (!token) {
  window.location.href = "login.html";
}

const user = userJson ? JSON.parse(userJson) : null;
if (user) {
  document.getElementById("user-info").textContent = `${user.name} (${user.role})`;
}

document.getElementById("logout-link").addEventListener("click", () => {
  localStorage.removeItem("admin_token");
  localStorage.removeItem("admin_user");
  window.location.href = "login.html";
});

function authHeaders(extra = {}) {
  return { Authorization: `Bearer ${token}`, ...extra };
}

// Any 401 means the token expired or is invalid - bounce to login.
async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { ...(options.headers || {}), ...authHeaders() },
  });
  if (res.status === 401) {
    localStorage.removeItem("admin_token");
    localStorage.removeItem("admin_user");
    window.location.href = "login.html";
    throw new Error("Session expired");
  }
  return res;
}

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const uploadError = document.getElementById("upload-error");
const docsBody = document.getElementById("docs-body");
const emptyState = document.getElementById("empty-state");
const docsTable = document.getElementById("docs-table");

dropzone.addEventListener("click", () => fileInput.click());

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) uploadFile(fileInput.files[0]);
  fileInput.value = "";
});

let pollTimer = null;

function hasUnfinishedDocuments(docs) {
  return docs.some((d) => d.status === "pending" || d.status === "processing");
}

function startPolling() {
  if (pollTimer) return; // already polling
  pollTimer = setInterval(async () => {
    const stillUnfinished = await loadDocuments();
    if (!stillUnfinished) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }, 2000);
}

async function uploadFile(file) {
  uploadError.textContent = "";
  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await apiFetch("/admin/documents/upload", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) {
      uploadError.textContent = data.detail || "Upload failed.";
      return;
    }
    // Upload returns immediately (202) - the file is still being processed
    // in the background. Refresh now to show it as pending, then poll
    // until it resolves to success/fail.
    await loadDocuments();
    startPolling();
  } catch (err) {
    if (err.message !== "Session expired") {
      uploadError.textContent = "Upload failed - could not reach the server.";
    }
  }
}

async function retryDocument(id, btn) {
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Retrying...";
  try {
    await apiFetch(`/admin/documents/${id}/retry`, { method: "POST" });
    await loadDocuments();
    startPolling();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

const modalOverlay = document.getElementById("modal-overlay");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");
const modalClose = document.getElementById("modal-close");

modalClose.addEventListener("click", closeModal);
modalOverlay.addEventListener("click", (e) => {
  if (e.target === modalOverlay) closeModal();
});

function closeModal() {
  modalOverlay.classList.remove("open");
}

async function viewChunks(id, filename) {
  modalTitle.textContent = filename;
  modalBody.innerHTML = "<p>Loading...</p>";
  modalOverlay.classList.add("open");

  try {
    const res = await apiFetch(`/admin/documents/${id}/chunks`);
    const data = await res.json();

    if (!data.chunks || data.chunks.length === 0) {
      modalBody.innerHTML = "<p>No chunks stored for this document.</p>";
      return;
    }

    modalBody.innerHTML = data.chunks
      .map(
        (c) => `
          <div class="chunk-block">
            <div class="chunk-label">Chunk ${c.chunk_index}</div>
            <div class="chunk-text">${escapeHtml(c.text)}</div>
          </div>
        `
      )
      .join("");
  } catch (err) {
    if (err.message !== "Session expired") {
      modalBody.innerHTML = "<p>Failed to load chunks.</p>";
    }
  }
}

function statusBadge(status) {
  const labels = { pending: "Pending", processing: "Processing", success: "Success", fail: "Failed" };
  return `<span class="status-badge status-${status}">${labels[status] || status}</span>`;
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString();
}

async function loadDocuments() {
  const res = await apiFetch("/admin/documents");
  const data = await res.json();
  const docs = data.documents || [];

  if (docs.length === 0) {
    docsTable.style.display = "none";
    emptyState.style.display = "block";
    return false;
  }
  docsTable.style.display = "table";
  emptyState.style.display = "none";

  docsBody.innerHTML = docs
    .map((d) => {
      const errorRow = d.error_message
        ? `<div class="error-detail">${escapeHtml(d.error_message)}</div>`
        : "";
      const retryBtn =
        d.status === "fail" || d.status === "processing" || d.status === "pending"
          ? `<button class="btn-small" onclick="retryDocument(${d.id}, this)">Retry</button>`
          : "";
      const viewLink =
        d.status === "success"
          ? `<span class="view-link" onclick="viewChunks(${d.id}, '${escapeHtml(d.filename)}')">View</span>`
          : "";
      return `
        <tr>
          <td>${escapeHtml(d.filename)}</td>
          <td>${d.file_type.toUpperCase()}</td>
          <td>${statusBadge(d.status)}${errorRow}</td>
          <td>${d.chunk_count ?? "—"}${viewLink}</td>
          <td>${formatDate(d.uploaded_at)}</td>
          <td>${escapeHtml(d.uploaded_by || "—")}</td>
          <td>${retryBtn}</td>
        </tr>
      `;
    })
    .join("");

  return hasUnfinishedDocuments(docs);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

loadDocuments().then((unfinished) => {
  if (unfinished) startPolling();
});
