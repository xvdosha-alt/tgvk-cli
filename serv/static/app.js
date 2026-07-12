const $ = (id) => document.getElementById(id);

const authSection = $("auth-section");
const panelSection = $("panel-section");
const alertEl = $("alert");

const authTabs = $("auth-tabs");
const tabQr = $("tab-qr");
const tabPhone = $("tab-phone");
const formQr = $("form-qr");
const formPhone = $("form-phone");
const formCode = $("form-code");
const formPassword = $("form-password");
const formSettings = $("form-settings");

let state = null;
let csrfToken = "";
let qrPollTimer = null;
let authMethod = "phone";

function isDesktop() {
  return window.matchMedia("(min-width: 768px) and (hover: hover)").matches;
}

function showAlert(text, type = "error") {
  alertEl.textContent = text;
  alertEl.className = `alert ${type}`;
  alertEl.classList.remove("hidden");
  setTimeout(() => alertEl.classList.add("hidden"), 5000);
}

function setLoading(loading) {
  document.querySelectorAll("button").forEach((btn) => {
    if (loading) btn.setAttribute("disabled", "disabled");
    else btn.removeAttribute("disabled");
  });
}

async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = {
    "Content-Type": "application/json",
    ...(opts.headers || {}),
  };
  if (method !== "GET" && method !== "HEAD" && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  const res = await fetch(path, {
    credentials: "same-origin",
    headers,
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const msg = typeof detail === "string"
      ? detail
      : Array.isArray(detail) && detail[0]?.msg
        ? detail[0].msg
        : res.statusText;
    if (res.status === 401) {
      setTimeout(() => location.reload(), 2000);
    }
    throw new Error(msg);
  }
  if (data.csrf_token) csrfToken = data.csrf_token;
  return data;
}

function stopQrPoll() {
  if (qrPollTimer) {
    clearInterval(qrPollTimer);
    qrPollTimer = null;
  }
}

function setQrImage(src) {
  const img = $("qr-image");
  if (src) img.src = src;
}

function setAuthTabsVisible(visible) {
  authTabs.classList.toggle("hidden", !visible);
}

function setAuthMethod(method) {
  authMethod = method;
  tabQr.classList.toggle("active", method === "qr");
  tabPhone.classList.toggle("active", method === "phone");
  formQr.classList.toggle("hidden", method !== "qr");
  formPhone.classList.toggle("hidden", method !== "phone");
  if (method === "qr") {
    $("auth-hint").textContent = "Сканируй QR-код в приложении Telegram на телефоне.";
  } else {
    $("auth-hint").textContent = "Введи номер — код придёт в Telegram.";
    stopQrPoll();
  }
}

function showAuthStep(step) {
  const onDesktop = isDesktop();
  const showTabs = onDesktop && (step === "phone" || step === "qr");
  setAuthTabsVisible(showTabs);

  if (step === "qr") {
    authMethod = "qr";
    tabQr.classList.add("active");
    tabPhone.classList.remove("active");
    formQr.classList.remove("hidden");
    formPhone.classList.add("hidden");
    formCode.classList.add("hidden");
    formPassword.classList.add("hidden");
    return;
  }

  if (step === "phone" && showTabs) {
    setAuthMethod(authMethod);
  } else {
    formQr.classList.add("hidden");
    formPhone.classList.toggle("hidden", step !== "phone");
  }
  formCode.classList.toggle("hidden", step !== "code");
  formPassword.classList.toggle("hidden", step !== "password");
}

function showAuthUI(step, hint) {
  authSection.classList.remove("hidden");
  panelSection.classList.add("hidden");
  showAuthStep(step);
  if (hint) $("auth-hint").textContent = hint;
}

async function startQr({ poll = true } = {}) {
  stopQrPoll();
  setLoading(true);
  try {
    const data = await api("/api/auth/qr/start", { method: "POST" });
    setQrImage(data.qr_image);
    showAuthUI("qr");
    if (poll) startQrPoll();
  } catch (err) {
    showAlert(err.message);
    if (isDesktop()) setAuthMethod("phone");
  } finally {
    setLoading(false);
  }
}

function startQrPoll() {
  stopQrPoll();
  qrPollTimer = setInterval(async () => {
    try {
      const data = await api("/api/auth/qr/status");
      if (data.needs_password) {
        stopQrPoll();
        showAuthUI("password", "Введи пароль 2FA из Telegram");
        state = { ...state, ...data, telegram_linked: false, auth_step: "password" };
        return;
      }
      if (data.telegram_linked || (data.auth_step === "done" && data.tg_name)) {
        stopQrPoll();
        showAlert("Telegram подключён", "ok");
        render(data);
        return;
      }
      if (data.qr_image) setQrImage(data.qr_image);
    } catch (err) {
      if (
        err.message.includes("истек") ||
        err.message.includes("не запущен") ||
        err.message.includes("отменён")
      ) {
        stopQrPoll();
        if (state?.auth_step === "qr" || authMethod === "qr") {
          startQr({ poll: true });
        }
      }
    }
  }, 2000);
}

function render(user) {
  state = user;
  if (user.csrf_token) csrfToken = user.csrf_token;

  if (!user.telegram_linked) {
    const step = user.auth_step || "phone";
    const hint =
      step === "code" ? "Введи код из Telegram" :
      step === "password" ? "Введи пароль 2FA из Telegram" :
      step === "qr" ? "Сканируй QR-код в приложении Telegram на телефоне." :
      "Введи номер — код придёт в Telegram.";
    showAuthUI(step, hint);

    if (step === "qr") {
      if (!$("qr-image").src) startQr({ poll: true });
      else startQrPoll();
    } else if (step === "phone" && isDesktop() && authMethod === "qr" && !qrPollTimer) {
      startQr({ poll: true });
    }
    return;
  }

  stopQrPoll();
  authSection.classList.add("hidden");
  panelSection.classList.remove("hidden");

  const name = user.tg_name || "—";
  const uname = user.tg_username ? ` @${user.tg_username}` : "";
  $("tg-info").textContent = name + uname;

  const badge = $("service-status");
  if (user.service_running) {
    badge.textContent = "работает";
    badge.className = "badge on";
  } else {
    badge.textContent = user.service_error ? "ошибка" : "остановлен";
    badge.className = "badge off";
  }

  $("vk-peer-id").value = user.vk_peer_id || "";
  $("img-mode").checked = !!user.img_mode;
  const useDefaultVk = !user.has_custom_vk_token;
  $("use-default-vk").checked = useDefaultVk;

  const vkWrap = $("vk-token-wrap");
  const vkInput = $("vk-token");
  vkWrap.classList.toggle("hidden", useDefaultVk);
  vkInput.value = "";
  vkInput.placeholder = user.has_custom_vk_token ? "Свой токен задан" : "Токен сообщества";

  $("btn-start").disabled = !user.ready || user.service_running;
  $("btn-stop").disabled = !user.service_running;

  if (user.service_error) showAlert(user.service_error, "error");
}

async function refresh() {
  const user = await api("/api/me");
  render(user);
}

tabQr.addEventListener("click", () => {
  if (authMethod === "qr") return;
  setAuthMethod("qr");
  startQr({ poll: true });
});

tabPhone.addEventListener("click", () => {
  if (authMethod === "phone") return;
  setAuthMethod("phone");
});

$("btn-qr-refresh").addEventListener("click", () => {
  startQr({ poll: true });
});

formPhone.addEventListener("submit", async (e) => {
  e.preventDefault();
  stopQrPoll();
  setLoading(true);
  try {
    await api("/api/auth/phone", {
      method: "POST",
      body: JSON.stringify({ phone: $("phone").value }),
    });
    showAlert("Код отправлен", "ok");
    await refresh();
  } catch (err) {
    showAlert(err.message);
  } finally {
    setLoading(false);
  }
});

formCode.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (formCode.dataset.busy === "1") return;
  formCode.dataset.busy = "1";
  setLoading(true);
  try {
    const data = await api("/api/auth/code", {
      method: "POST",
      body: JSON.stringify({ code: $("code").value.trim() }),
    });
    if (data.needs_password) {
      showAuthUI("password", "Введи пароль 2FA из Telegram");
      state = { ...state, ...data, telegram_linked: false, auth_step: "password" };
      return;
    }
    showAlert("Telegram подключён", "ok");
    render(data);
  } catch (err) {
    showAlert(err.message);
  } finally {
    formCode.dataset.busy = "0";
    setLoading(false);
  }
});

formPassword.addEventListener("submit", async (e) => {
  e.preventDefault();
  setLoading(true);
  try {
    const data = await api("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({ password: $("password").value }),
    });
    showAlert("Telegram подключён", "ok");
    render(data);
  } catch (err) {
    showAlert(err.message);
  } finally {
    setLoading(false);
  }
});

formSettings.addEventListener("submit", async (e) => {
  e.preventDefault();
  setLoading(true);
  const body = {
    vk_peer_id: parseInt($("vk-peer-id").value, 10) || null,
    img_mode: $("img-mode").checked,
    use_default_vk_token: $("use-default-vk").checked,
  };
  const token = $("vk-token").value.trim();
  if (token) body.vk_token = token;
  try {
    const data = await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    showAlert("Сохранено", "ok");
    render(data);
  } catch (err) {
    showAlert(err.message);
  } finally {
    setLoading(false);
  }
});

$("use-default-vk").addEventListener("change", () => {
  const useDefault = $("use-default-vk").checked;
  $("vk-token-wrap").classList.toggle("hidden", useDefault);
  if (useDefault) $("vk-token").value = "";
});

$("btn-start").addEventListener("click", async () => {
  setLoading(true);
  try {
    const data = await api("/api/service/start", { method: "POST" });
    showAlert("Сервис запущен", "ok");
    render(data);
  } catch (err) {
    showAlert(err.message);
  } finally {
    setLoading(false);
  }
});

$("btn-stop").addEventListener("click", async () => {
  setLoading(true);
  try {
    const data = await api("/api/service/stop", { method: "POST" });
    showAlert("Сервис остановлен", "ok");
    render(data);
  } catch (err) {
    showAlert(err.message);
  } finally {
    setLoading(false);
  }
});

$("btn-logout").addEventListener("click", async () => {
  if (!confirm("Выйти из Telegram? Сервис будет остановлен.")) return;
  stopQrPoll();
  setLoading(true);
  try {
    const data = await api("/api/auth/logout", { method: "POST" });
    showAlert("Выход выполнен", "ok");
    authMethod = isDesktop() ? "qr" : "phone";
    render(data);
  } catch (err) {
    showAlert(err.message);
  } finally {
    setLoading(false);
  }
});

(async () => {
  if (isDesktop()) authMethod = "qr";
  try {
    await refresh();
  } catch (err) {
    showAlert(err.message);
  }
})();
