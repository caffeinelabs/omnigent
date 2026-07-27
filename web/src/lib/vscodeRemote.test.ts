import { describe, expect, it } from "vitest";
import { buildSshPiperUsername, buildVsCodeRemoteUri } from "./vscodeRemote";

describe("buildSshPiperUsername", () => {
  it("joins target and user with --", () => {
    expect(buildSshPiperUsername("demo-workspace.sshpiper-demo.svc.cluster.local", "dev")).toBe(
      "demo-workspace.sshpiper-demo.svc.cluster.local--dev",
    );
  });
});

describe("buildVsCodeRemoteUri", () => {
  it("builds an encoded vscode-remote URI", () => {
    const uri = buildVsCodeRemoteUri({
      sshpiperHost: "sshpiper.example.com",
      sshTarget: "demo-workspace.sshpiper-demo.svc.cluster.local",
      sshUser: "dev",
      workspacePath: "/home/omnigent/workspace/repo",
    });
    expect(uri).toBe(
      "vscode://vscode-remote/ssh-remote+" +
        encodeURIComponent(
          "demo-workspace.sshpiper-demo.svc.cluster.local--dev@sshpiper.example.com",
        ) +
        "/home/omnigent/workspace/repo",
    );
  });

  it("uses a prebuilt sshpiper username when provided", () => {
    const uri = buildVsCodeRemoteUri({
      sshpiperHost: "sshpiper.example.com",
      sshpiperUsername: "pod.ns.svc.cluster.local--sandbox",
      workspacePath: "/root/workspace",
    });
    expect(uri).toContain(
      encodeURIComponent("pod.ns.svc.cluster.local--sandbox@sshpiper.example.com"),
    );
  });

  it("appends a non-default port", () => {
    const uri = buildVsCodeRemoteUri({
      sshpiperHost: "sshpiper.example.com",
      sshpiperPort: 2222,
      sshpiperUsername: "target--sandbox",
      workspacePath: "/root/workspace",
    });
    expect(uri).toContain(encodeURIComponent("target--sandbox@sshpiper.example.com:2222"));
  });

  it("returns null for a relative workspace path", () => {
    expect(
      buildVsCodeRemoteUri({
        sshpiperHost: "sshpiper.example.com",
        sshpiperUsername: "t--u",
        workspacePath: "workspace",
      }),
    ).toBeNull();
  });

  it("returns null when the gateway is missing", () => {
    expect(
      buildVsCodeRemoteUri({
        sshpiperHost: "",
        sshpiperUsername: "t--u",
        workspacePath: "/root/workspace",
      }),
    ).toBeNull();
  });
});
