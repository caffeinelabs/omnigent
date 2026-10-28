// Pure People logic: role names and what each may do, roster order, who the
// viewer may change, invite validation and "last seen" wording.

import type { PersonRole, PersonView, ViewerBlock } from "@/lib/factoryApi";
import { ageText } from "../board/boardModel";
import { presenceWhere } from "../shell/shellRoutes";

export const PERSON_ROLES: readonly PersonRole[] = ["viewer", "operator", "owner"];

export const ROLE_LABEL: Record<PersonRole, string> = {
  viewer: "Viewer",
  operator: "Operator",
  owner: "Owner",
};

/** What each role adds, as the Share dialog's legend says it. */
export const ROLE_POWERS: Record<PersonRole, string> = {
  viewer: "watch everything",
  operator: "chat, answer, apply plans, steer agents",
  owner: "+ people, stop, restart, budgets, improvements",
};

const ROLE_RANK: Record<PersonRole, number> = { owner: 0, operator: 1, viewer: 2 };

/** The creator first, then owners, operators and viewers, each by name. */
export function orderRoster(people: readonly PersonView[]): PersonView[] {
  return [...people].sort(
    (a, b) =>
      Number(b.is_creator) - Number(a.is_creator) ||
      ROLE_RANK[a.role] - ROLE_RANK[b.role] ||
      (a.name ?? a.user_id).localeCompare(b.name ?? b.user_id),
  );
}

/** What the viewer may do to `person`'s row: change the role, remove, or leave. */
export function personLevers(
  person: PersonView,
  viewer: ViewerBlock | null | undefined,
): { changeRole: boolean; remove: boolean; leave: boolean } {
  const self = viewer?.user_id != null && viewer.user_id === person.user_id;
  const manage = viewer?.can.invite === true;
  return {
    // Nobody changes the creator; owners never change their own role.
    changeRole: manage && !person.is_creator && !self,
    remove: manage && !person.is_creator && !self,
    leave: self && !person.is_creator,
  };
}

const EMAIL = /^[^\s@/\\]+@[^\s@/\\]+\.[^\s@/\\]+$/;

/** Why `value` can't be invited, or null when it can. */
export function inviteError(value: string, roster: readonly PersonView[]): string | null {
  const email = value.trim();
  if (!email) return "Enter an email";
  if (!EMAIL.test(email)) return "Use the person’s sign-in email (GitHub primary email)";
  const existing = roster.find((p) => p.user_id.toLowerCase() === email.toLowerCase());
  if (existing)
    return `${existing.name ?? existing.user_id} is already ${ROLE_LABEL[existing.role].toLowerCase()}`;
  return null;
}

/** `"● Overview"`, `"● idle · db-version page"`, `"○ 2h ago"`, `"○ never"`. */
export function presenceText(person: PersonView, nowS: number): { here: boolean; text: string } {
  if (person.present) {
    const where = presenceWhere(person.location);
    return { here: true, text: person.idle ? `idle · ${where}` : where };
  }
  if (person.last_seen_at == null) return { here: false, text: "not here yet" };
  return { here: false, text: ageText(nowS - person.last_seen_at) };
}
