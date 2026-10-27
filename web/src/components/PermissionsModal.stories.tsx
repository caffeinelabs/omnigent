import type { Decorator, Meta, StoryObj } from "@storybook/react-vite";
import { fireEvent } from "storybook/test";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";
import { FALLBACK_SERVER_INFO, type SharingMode } from "@/lib/capabilities";
import type { Permission } from "@/lib/permissionsApi";
import { StoryQueryRouter } from "@/storybook/StoryProviders";
import { PermissionsModal } from "./PermissionsModal";

const sessionId = "conversation-permissions-story";
const FACTORY_ID = "f4f4ba9c77725331b12f0fa7072dc4d3";

function modalEnvironment({
  mode,
  permissions = [],
  publicSharing = true,
  labels,
}: {
  mode: SharingMode;
  permissions?: Permission[];
  publicSharing?: boolean;
  /** Session labels, e.g. a factory member's `omnigent.factory.id`. */
  labels?: Record<string, string>;
}): Decorator {
  return (Story) => (
    <CapabilitiesProvider
      info={{
        ...FALLBACK_SERVER_INFO,
        sharing_mode: mode,
        public_sharing_enabled: publicSharing,
      }}
    >
      <StoryQueryRouter
        route={`/c/${sessionId}`}
        seed={(queryClient) => {
          queryClient.setQueryData(["permissions", sessionId], permissions);
          if (labels) {
            queryClient.setQueryData(["session", sessionId], { id: sessionId, labels });
            queryClient.setQueryData(["factories"], {
              data: [{ id: FACTORY_ID, name: "Atlas factory" }],
            });
          }
        }}
      >
        <Story />
      </StoryQueryRouter>
    </CapabilitiesProvider>
  );
}

const meta = {
  title: "Components/Sharing/PermissionsModal",
  component: PermissionsModal,
  tags: ["visual-snapshot"],
  args: { sessionId, open: true, onOpenChange: () => undefined },
} satisfies Meta<typeof PermissionsModal>;

export default meta;
type Story = StoryObj<typeof meta>;

export const SharingDisabled: Story = {
  decorators: [modalEnvironment({ mode: "off" })],
};

export const GrantSpectrum: Story = {
  decorators: [
    modalEnvironment({
      mode: "on",
      permissions: [
        { user_id: "owner@example.com", conversation_id: sessionId, level: 4 },
        {
          user_id: "alexandra.reyes@very-long-company-domain.example.com",
          conversation_id: sessionId,
          level: 3,
        },
        { user_id: "bob@example.com", conversation_id: sessionId, level: 2 },
        { user_id: "carol@example.com", conversation_id: sessionId, level: 1 },
      ],
    }),
  ],
  play: async ({ canvasElement }) => {
    const input = canvasElement.ownerDocument.querySelector<HTMLInputElement>("#perm-user");
    if (!input) throw new Error("Permission user input not found");
    await fireEvent.change(input, { target: { value: "dave@example.com" } });
  },
};

export const PublicReadOnly: Story = {
  decorators: [
    modalEnvironment({
      mode: "read_only",
      permissions: [
        { user_id: "owner@example.com", conversation_id: sessionId, level: 4 },
        { user_id: "__public__", conversation_id: sessionId, level: 1 },
        { user_id: "alice@example.com", conversation_id: sessionId, level: 1 },
      ],
    }),
  ],
};

export const RestrictedHomeDirectory: Story = {
  args: { workspace: "/home/alice" },
  decorators: [
    modalEnvironment({
      mode: "restricted_read_only",
      permissions: [{ user_id: "owner@example.com", conversation_id: sessionId, level: 4 }],
      publicSharing: false,
    }),
  ],
  play: async ({ canvasElement }) => {
    const input = canvasElement.ownerDocument.querySelector<HTMLInputElement>("#perm-user");
    if (!input) throw new Error("Permission user input not found");
    await fireEvent.change(input, { target: { value: "viewer@example.com" } });
  },
};

/** A factory member: the roster on the factory's People page owns these grants. */
export const FactoryManaged: Story = {
  decorators: [
    modalEnvironment({
      mode: "on",
      labels: { "omnigent.factory.id": FACTORY_ID },
      permissions: [
        { user_id: "luka@caffeine.ai", conversation_id: sessionId, level: 4 },
        { user_id: "mia@caffeine.ai", conversation_id: sessionId, level: 2 },
        { user_id: "ops@caffeine.ai", conversation_id: sessionId, level: 1 },
      ],
    }),
  ],
};
