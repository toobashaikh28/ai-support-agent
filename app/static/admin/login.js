const form = document.getElementById("login-form");
const submitBtn = document.getElementById("submit-btn");
const errorText = document.getElementById("error-text");

// If already logged in, skip straight to the dashboard.
if (localStorage.getItem("admin_token")) {
  window.location.href = "dashboard.html";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorText.textContent = "";
  submitBtn.disabled = true;
  submitBtn.textContent = "Signing in...";

  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;

  try {
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();

    if (!res.ok) {
      errorText.textContent = data.detail || "Login failed.";
      return;
    }

    localStorage.setItem("admin_token", data.token);
    localStorage.setItem("admin_user", JSON.stringify(data.user));
    window.location.href = "dashboard.html";
  } catch (err) {
    errorText.textContent = "Could not reach the server. Is the API running?";
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Sign in";
  }
});
