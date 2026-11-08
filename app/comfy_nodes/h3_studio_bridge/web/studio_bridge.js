// ?h3_job=<prompt id> (Studio "Open in ComfyUI"): load that job's exact graph into the editor.
// ?h3_workflow=<inbox id>: a Studio custom workflow opened for editing; it is sent back to the same workflow.
// loadApiJson is the frontend's own API→workflow conversion, the same as dropping the JSON file.
// "Send to Studio" returns the edited graph; Studio shows the changes and renders it.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// The workflow opened from Studio is named «Studio <prompt id>»: the frontend rebuilds graph.extra
// after loading, while the name stays with the tab, its save and a page reload.
const SOURCE_NAME = /^Studio ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/;
const sourceJob = () => app.extensionManager?.workflow?.activeWorkflow?.filename?.match(SOURCE_NAME)?.[1];

function notify(detail, severity = "error") {
  const toast = app.extensionManager?.toast;
  if (toast) toast.add({ severity, summary: "Studio", detail, life: 10000 });
  else alert(detail);
}

async function sendToStudio(button) {
  const source = sourceJob();
  if (!source) return;
  button.disabled = true;
  try {
    const { output } = await app.graphToPrompt();
    const response = await api.fetchApi("/h3/studio/edited", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_prompt_id: source, prompt: output }),
    });
    if (!response.ok) throw new Error(await response.text());
    notify("Edit sent. Open the job card in Studio to review the ComfyUI changes.", "success");
  } catch (error) {
    notify(`Could not send the edit: ${error.message || error}`);
  } finally {
    button.disabled = false;
  }
}

function sendButton() {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "⇪ Send to Studio";
  button.title = "Send this graph back to Studio as a job variant";
  Object.assign(button.style, {
    position: "fixed", top: "8px", left: "50%", transform: "translateX(-50%)", zIndex: 1000,
    padding: "6px 14px", borderRadius: "8px", border: "1px solid #c9a24a",
    background: "#1d1a14", color: "#f2e3bf", font: "600 13px system-ui", cursor: "pointer",
  });
  button.addEventListener("click", () => sendToStudio(button));
  // Shown only while the open workflow came from Studio.
  setInterval(() => { button.style.display = sourceJob() ? "" : "none"; }, 1000);
  document.body.append(button);
}

// "Workflow to Studio": any open graph becomes a Studio workflow (form, API, agent tool) without file export.
// A workflow opened from Studio (?h3_workflow=<id>) is named «Studio wf_<id> <name>» and goes back to it.
const STUDIO_WORKFLOW = /^Studio (wf_[0-9a-f]{12}) ?(.*?)(\.json)?$/;
async function sendWorkflow(button) {
  const current = app.extensionManager?.workflow?.activeWorkflow?.filename || "ComfyUI workflow";
  const own = current.match(STUDIO_WORKFLOW);
  const name = window.prompt(own ? "Update Studio workflow (name)" : "Workflow name in Studio",
    own ? (own[2] || own[1]) : current.replace(/\.json$/i, ""));
  if (!name) return;
  button.disabled = true;
  try {
    const { output } = await app.graphToPrompt();
    const response = await api.fetchApi("/h3/studio/workflows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, prompt: output, ...(own ? { studio_id: own[1] } : {}) }),
    });
    if (!response.ok) throw new Error(await response.text());
    notify(own ? "Sent. In Studio: Custom workflows → From ComfyUI → Update."
      : "Workflow sent. In Studio: Custom workflows → From ComfyUI.", "success");
  } catch (error) {
    notify(`Could not send the workflow: ${error.message || error}`);
  } finally {
    button.disabled = false;
  }
}

function workflowButton() {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "⇪ Workflow to Studio";
  button.title = "Save the open graph as a Studio workflow, form, API endpoint and agent tool";
  Object.assign(button.style, {
    position: "fixed", top: "8px", right: "180px", zIndex: 1000,
    padding: "6px 14px", borderRadius: "8px", border: "1px solid #5f7f6a",
    background: "#141a16", color: "#dfeee3", font: "600 13px system-ui", cursor: "pointer",
  });
  button.addEventListener("click", () => sendWorkflow(button));
  document.body.append(button);
}

app.registerExtension({
  name: "studio.studioBridge",
  async setup() {
    sendButton();
    workflowButton();
    const url = new URL(window.location.href);
    const workflowId = url.searchParams.get("h3_workflow");
    if (workflowId) {
      url.searchParams.delete("h3_workflow");
      history.replaceState(history.state, "", url);
      const response = await api.fetchApi(`/h3/studio/workflows/${encodeURIComponent(workflowId)}`);
      if (!response.ok) { notify(`Could not open the Studio workflow: HTTP ${response.status}`); return; }
      const sent = await response.json();
      await app.loadApiJson(sent.prompt, sent.studio_id ? `Studio ${sent.studio_id} ${sent.name || ""}`.trim() : sent.name);
      return;
    }
    const id = url.searchParams.get("h3_job");
    if (!id) return;
    url.searchParams.delete("h3_job");
    history.replaceState(history.state, "", url);
    const response = await api.fetchApi(`/h3/studio/graph/${encodeURIComponent(id)}`);
    if (!response.ok) {
      notify(response.status === 404
        ? `Job graph ${id} was not found in this ComfyUI instance (another server or still queued on the Mac).`
        : `Could not open job graph ${id}: HTTP ${response.status}`);
      return;
    }
    const { prompt } = await response.json();
    await app.loadApiJson(prompt, `Studio ${id}`);
  },
});
