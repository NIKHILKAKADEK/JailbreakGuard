const form = document.querySelector("#scan-form");
const promptText = document.querySelector("#prompt-text");
const errorMessage = document.querySelector("#form-error");
const resultsContent = document.querySelector("#results-content");
const emptyResults = document.querySelector("#empty-results");
const historyList = document.querySelector("#history-list");
const scanCount = document.querySelector("#scan-count");

promptText.addEventListener("input", () => {
  document.querySelector("#character-count").textContent = `${promptText.value.length.toLocaleString()} / 10,000`;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorMessage.textContent = "";
  const text = promptText.value.trim();
  if (!text) {
    errorMessage.textContent = "Please enter a prompt before scanning.";
    promptText.focus();
    return;
  }

  const button = document.querySelector("#scan-button");
  button.disabled = true;
  button.querySelector("span").textContent = "Scanning...";
  try {
    const response = await fetch("/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The scan could not be completed.");
    renderResult(result);
    addHistory(result);
  } catch (error) {
    errorMessage.textContent = error.message;
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "Scan prompt";
  }
});

function renderResult(result) {
  emptyResults.classList.add("hidden");
  resultsContent.classList.remove("hidden");
  const status = document.querySelector(".result-status");
  status.className = `result-status ${result.level}`;
  document.querySelector("#result-status").textContent = result.status;
  document.querySelector("#result-explanation").textContent = result.explanation;
  const evidenceList = document.querySelector("#evidence-list");
  evidenceList.replaceChildren();
  if (!result.evidence.length) {
    const item = document.createElement("li");
    item.textContent = "No starter rule matched.";
    evidenceList.append(item);
  } else {
    result.evidence.forEach((match) => {
      const item = document.createElement("li");
      item.textContent = match.evidence;
      evidenceList.append(item);
    });
  }
}

function addHistory(result) {
  const empty = historyList.querySelector(".history-empty");
  if (empty) empty.remove();
  const item = document.createElement("div");
  item.className = "history-item";
  const title = document.createElement("strong");
  title.className = result.level;
  title.textContent = result.level === "warning" ? "Indicators found" : "No indicators";
  const time = document.createElement("time");
  time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  item.append(title, time);
  historyList.prepend(item);
  scanCount.textContent = historyList.children.length;
}
