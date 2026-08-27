const form = document.getElementById("login-form");
const submitBtn = document.getElementById("submit-btn");
const errorText = document.getElementById("error-text");

if (localStorage.getItem("user_token")) {
  window.location.href = "chat.html";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorText.textContent = "";
  submitBtn.disabled = true;
  submitBtn.textContent = "Logging in...";

  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;

  try {
    const res = await fetch("/auth/customer-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();

    if (!res.ok) {
      errorText.textContent = data.detail || "Login failed.";
      return;
    }

    localStorage.setItem("user_token", data.token);
    localStorage.setItem("user_info", JSON.stringify(data.user));
    window.location.href = "chat.html";
  } catch (err) {
    errorText.textContent = "Could not reach the server. Is the API running?";
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Log in";
  }
});
