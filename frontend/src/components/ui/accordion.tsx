import * as AccordionPrimitive from "@radix-ui/react-accordion";
import { ChevronDown } from "lucide-react";
import type { ComponentProps } from "react";

// Adapted from shadcn/ui Accordion and backed by Radix's keyboard-accessible primitive.
export function Accordion(props: ComponentProps<typeof AccordionPrimitive.Root>) {
  return <AccordionPrimitive.Root data-slot="accordion" {...props} />;
}

export function AccordionItem({
  className = "",
  ...props
}: ComponentProps<typeof AccordionPrimitive.Item>) {
  return (
    <AccordionPrimitive.Item
      data-slot="accordion-item"
      className={`accordion-item ${className}`.trim()}
      {...props}
    />
  );
}

export function AccordionTrigger({
  className = "",
  children,
  ...props
}: ComponentProps<typeof AccordionPrimitive.Trigger>) {
  return (
    <AccordionPrimitive.Header className="accordion-header">
      <AccordionPrimitive.Trigger
        data-slot="accordion-trigger"
        className={`accordion-trigger ${className}`.trim()}
        {...props}
      >
        <span>{children}</span>
        <ChevronDown aria-hidden="true" className="accordion-chevron" size={16} />
      </AccordionPrimitive.Trigger>
    </AccordionPrimitive.Header>
  );
}

export function AccordionContent({
  className = "",
  children,
  ...props
}: ComponentProps<typeof AccordionPrimitive.Content>) {
  return (
    <AccordionPrimitive.Content
      data-slot="accordion-content"
      className="accordion-content"
      {...props}
    >
      <div className={`accordion-content-inner ${className}`.trim()}>{children}</div>
    </AccordionPrimitive.Content>
  );
}
