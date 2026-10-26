import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button } from "./button";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "./sheet";

function Example({
  side = "right",
  size = "lg",
  open,
}: {
  side?: "right" | "bottom";
  size?: "md" | "lg" | "xl";
  open?: boolean;
}) {
  return (
    <Sheet defaultOpen={open}>
      <SheetTrigger asChild>
        <Button variant="outline">Open {side} sheet</Button>
      </SheetTrigger>
      <SheetContent side={side} size={size}>
        <SheetHeader>
          <SheetTitle>api-version · P1</SheetTitle>
          <SheetDescription>Ready · caffeinelabs/app · main</SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-3 text-sm">
          {Array.from({ length: 12 }, (_, i) => (
            <p key={i} className="text-muted-foreground">
              Row {i + 1}: the body scrolls while the header and footer stay put.
            </p>
          ))}
        </SheetBody>
        <SheetFooter>
          <Button variant="outline" size="sm">
            Cancel
          </Button>
          <Button size="sm">Save</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

const meta = {
  title: "UI/Sheet",
  component: Example,
  parameters: { layout: "centered" },
} satisfies Meta<typeof Example>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Closed: Story = { args: {} };
export const RightMedium: Story = { args: { side: "right", size: "md", open: true } };
export const RightLarge: Story = { args: { side: "right", size: "lg", open: true } };
export const RightExtraLarge: Story = { args: { side: "right", size: "xl", open: true } };
export const Bottom: Story = { args: { side: "bottom", size: "lg", open: true } };
