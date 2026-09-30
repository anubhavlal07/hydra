"use client";

import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { useIsPhone } from "@/lib/media";
import type { DeviceConfig } from "@/lib/types";
import { Button, CopyField, CopyRow, Group, RowBlock, Segmented } from "./ui";
import { QrCode } from "./ui/qr";

type Target = "setup" | "dot" | "doh";

interface Code {
  value: Target;
  label: string;
  name: string;
  text: string;
}

export function setupCodes(device: DeviceConfig): Code[] {
  const codes: (Code | null)[] = [
    device.setup_url
      ? { value: "setup", label: "Setup link", name: "Setup link", text: device.setup_url }
      : null,
    device.dot_hostname
      ? {
          value: "dot",
          label: "Private DNS",
          name: "Android Private DNS hostname",
          text: device.dot_hostname,
        }
      : null,
    { value: "doh", label: "DoH URL", name: "DoH URL", text: device.doh_url },
  ];
  return codes.filter((code): code is Code => code !== null);
}

export function SetupCodes({ device }: { device: DeviceConfig }) {
  const phone = useIsPhone();
  const codes = setupCodes(device);
  const [picked, setPicked] = useState<Target>(codes[0].value);
  const code = codes.find((entry) => entry.value === picked) ?? codes[0];

  const picker =
    codes.length > 1 ? (
      <Segmented
        items={codes.map(({ value, label }) => ({ value, label }))}
        value={code.value}
        onChange={setPicked}
        label={`QR code for ${device.name}`}
        className={phone ? "w-full" : "self-start"}
      />
    ) : null;

  const qr = (
    <QrCode
      value={code.text}
      label={`QR code: ${code.name}`}
      className={phone ? "mx-auto w-full max-w-60" : "w-48"}
    />
  );

  const open = (
    <Button
      asChild
      variant={phone ? "primary" : "default"}
      className={phone ? "w-full" : "self-start"}
    >
      <a href={code.text} target="_blank" rel="noopener noreferrer">
        <ExternalLink />
        Open setup link
      </a>
    </Button>
  );

  if (phone) {
    return (
      <Group heading="Scan or open">
        {picker && <RowBlock>{picker}</RowBlock>}
        {code.value === "setup" && <RowBlock>{open}</RowBlock>}
        <CopyRow title={code.name} value={code.text} />
        <RowBlock>{qr}</RowBlock>
      </Group>
    );
  }

  return (
    <div data-slot="setup-codes" className="flex min-w-0 flex-col gap-3">
      {picker}
      {qr}
      <CopyField value={code.text} label={code.name} />
      {code.value === "setup" && open}
    </div>
  );
}
