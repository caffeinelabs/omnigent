// Runs a parsed Room slash command against the route it maps to. Controls go
// through the shared controls store (so the project's pending chip shows
// everywhere); the rest call their routes directly. Every outcome comes back
// as a short note for the thread ("only you see this"); `/status` returns
// the server's digest.

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import { putFactoryPerson } from "@/lib/factoryApi";
import {
  ackRoomIssue,
  addFactoryTask,
  assignDecision,
  claimDecision,
  fetchStatusDigest,
  sendIntervention,
} from "@/lib/factoryRoomApi";
import type { RoomCommand } from "@/lib/roomCommands";
import { controlErrorMessage } from "../controls/controlModel";
import { useFactoryControls } from "../controls/useFactoryControls";

/** What running a command left for the thread. */
export interface CommandNote {
  ok: boolean;
  text: string;
  /** Markdown the server rendered (`/status`). */
  markdown?: string | null;
}

export interface RoomCommandHandlers {
  /** `/project new [title]`: Add project, prefilled. */
  openAddProject: (title: string | null) => void;
  /** `/charter <slug>`: Edit charter. */
  openCharter: (slug: string) => void;
}

export function useRoomCommandRunner(factoryId: string, handlers: RoomCommandHandlers) {
  const queryClient = useQueryClient();
  const controls = useFactoryControls(factoryId);
  const { openAddProject, openCharter } = handlers;

  const refresh = useCallback(() => {
    for (const key of factoryInvalidationKeys(factoryId)) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  }, [factoryId, queryClient]);

  return useCallback(
    async (command: RoomCommand): Promise<CommandNote | null> => {
      const attempt = async (call: () => Promise<unknown>, done: string): Promise<CommandNote> => {
        try {
          await call();
          refresh();
          return { ok: true, text: done };
        } catch (error) {
          return { ok: false, text: controlErrorMessage(error) };
        }
      };
      switch (command.kind) {
        case "project_new":
          openAddProject(command.title);
          return null;
        case "charter":
          openCharter(command.slug);
          return null;
        case "control": {
          const res = await controls.send(command.request, { label: command.label });
          return res
            ? { ok: true, text: `${command.label} · sent; the receipt lands here` }
            : { ok: false, text: `${command.label} was refused` };
        }
        case "intervene":
          return attempt(
            () =>
              sendIntervention(factoryId, {
                action: command.action,
                target: command.target,
                ...(command.note ? { note: command.note } : {}),
              }),
            command.action === "message"
              ? `Message sent to ${command.agent}; its PL is told`
              : `Interrupted ${command.agent}; its PL is told`,
          );
        case "task":
          return attempt(
            () =>
              addFactoryTask(factoryId, {
                project: command.slug,
                key: command.key,
                title: command.title,
              }),
            `Task ${command.key} added to #${command.slug}; its PL gets a TASK ADD`,
          );
        case "assign":
          return attempt(
            () => assignDecision(command.decision.id, command.to),
            `${command.decision.ref} assigned to ${command.label}`,
          );
        case "claim":
          return attempt(
            () => claimDecision(command.decision.id),
            `You claimed ${command.decision.ref}`,
          );
        case "ack":
          return attempt(() => ackRoomIssue(factoryId, command.ref), `${command.ref} acknowledged`);
        case "invite":
          return attempt(
            () => putFactoryPerson(factoryId, command.email, command.role),
            `${command.email} invited as ${command.role}`,
          );
        case "status":
          try {
            const digest = await fetchStatusDigest(factoryId, command.slug);
            return {
              ok: true,
              text: command.slug ? `Status of #${command.slug}` : "Factory status",
              markdown: digest.markdown,
            };
          } catch (error) {
            return { ok: false, text: controlErrorMessage(error) };
          }
      }
    },
    [controls, factoryId, openAddProject, openCharter, refresh],
  );
}
