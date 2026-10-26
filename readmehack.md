# NVIDIA LaunchPad Development Environment

This NVIDIA LaunchPad Development Environment provides users with immediate access to a prescriptive labs and a development environment pre-configured with common resources, tools, and IDE extensions to maximize productivity. This particular deployment includes the following platform(s), components, and configurations:
- [NVIDIA Cloud Native Stack](https://github.com/NVIDIA/cloud-native-stack/tree/master/playbooks)
- [Code Server](https://github.com/coder/code-server/) with a prescriptive workspace

---

## Resources

- [Coder IDE](https://750cefc9-f28d-958a-7952-0081c2ccf03f.nvidialaunchpad.com/coder/)
    - This web-based IDE is the preferred method of interacting with your NVIDIA LaunchPad environment, with drag-and-drop uploads and easy download capabilities supported out-of-box. Note you may encounter filesize upload limits of ~100MB. It's preferred to import large datasets or object via `pull` (e.g. initiating a download from a terminal in Code Server from an externally hosted object store).
- [Desktop Environment](https://750cefc9-f28d-958a-7952-0081c2ccf03f.nvidialaunchpad.com/desktop)
    - If you require a full graphical user interface you can access the GPU node directly via NoVNC and your web browser.
- [WebSSH](https://750cefc9-f28d-958a-7952-0081c2ccf03f.nvidialaunchpad.com/ssh/host/)
    - WebSSH is provided as an alternative to exclusive use of the Code Server IDE. **If executing long running tasks, it's recommended to use the Desktop Environment or Code Server IDE to ensure the task completes successfully.**
- [Metrics](https://750cefc9-f28d-958a-7952-0081c2ccf03f.nvidialaunchpad.com/metrics/)
    - Grafana is included with a preconfigured dashboard and datasource to allow for easy review of environment resource utilization.

## Build with AI Coding Agents

Codex CLI and Claude Code are preinstalled in this environment. Open a terminal in Coder, choose a project, and use either agent to inspect code, make focused changes, and run the tools available on the system.

![Workflow: open a terminal, choose a project, start an agent, review changes, and run tests](agent-cli-workflow.svg)

```bash
codex --version
claude --version

cd /path/to/your/project
git status --short

# Start either interactive agent
codex
# or
claude
```

Start with a read-only orientation prompt:

```text
Inspect this repository. Explain its structure and identify the build and test commands. Do not edit anything yet.
```

When you are ready to make a change, give the agent a bounded goal and ask it to run the relevant tests. Never paste an API key, password, token, or other secret into an agent prompt.

Before committing or sharing the work, review the result:

```bash
git status --short
git diff --check
git diff
```

For the complete workflow, see [Using AI coding agents](/launch/docs/#/overview?id=build-with-ai-coding-agents).

## Getting Started

[NVIDIA Cloud Native Stack with Docker](https://github.com/NVIDIA/cloud-native-stack/tree/master/playbooks) allows the flexibility of deploying local docker workloads or Kubernetes workloads directly on a GPU node with appropriate drivers pre-installed for your convenience. To determine what hardware and specs are included in your sandbox you can execute the following commands:

### Docker
```bash
docker run --rm --gpus all nvidia/cuda:13.0.0-base-ubuntu22.04 nvidia-smi
```

### Kubernetes
```bash
kubectl exec daemonset/nvidia-device-plugin-daemonset -n nvidia-gpu-operator -- nvidia-smi
```

Sample output:
```
Defaulted container "nvidia-device-plugin" out of: nvidia-device-plugin, toolkit-validation (init)
Tue Sep  9 18:46:26 2025
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.65.06              Driver Version: 580.65.06      CUDA Version: 13.0     |
+-----------------------------------------+------------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|                                         |                        |               MIG M. |
|=========================================+========================+======================|
|   0  NVIDIA L40S                    Off |   00000000:01:00.0 Off |                    0 |
| N/A   40C    P8             38W /  350W |       0MiB /  46068MiB |      0%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+
|   1  NVIDIA L40S                    Off |   00000000:E1:00.0 Off |                    0 |
| N/A   42C    P8             37W /  350W |       0MiB /  46068MiB |      0%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+

+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|        ID   ID                                                               Usage      |
|=========================================================================================|
|  No running processes found                                                             |
+-----------------------------------------------------------------------------------------+
```
