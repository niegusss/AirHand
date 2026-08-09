import type { ReactNode } from 'react'

import { SettingSlider } from '@/components/settings/SettingSlider'
import { SettingToggle } from '@/components/settings/SettingToggle'
import { knobPatch, knobValue, type KnobSpec, type SettingsGroup } from '@/lib/settings'
import type { EngineSettings, SettingsBounds, SettingsPatch } from '@/lib/protocol'

interface SettingsSectionProps {
  group: SettingsGroup
  settings: EngineSettings
  bounds: SettingsBounds
  disabled?: boolean
  onCommit: (patch: SettingsPatch) => void
  /** Compound controls that belong to this group, rendered above its own knobs. */
  children?: ReactNode
}

function Knob({
  spec,
  settings,
  bounds,
  disabled,
  onCommit,
}: {
  spec: KnobSpec
  settings: EngineSettings
  bounds: SettingsBounds
  disabled?: boolean
  onCommit: (patch: SettingsPatch) => void
}) {
  const value = knobValue(settings, spec)
  const range = (bounds[spec.section] as Record<string, [number, number] | null>)[spec.knob]

  // The wire reports `null` bounds for exactly the fields that are booleans, so the value's type
  // and the absence of a range always agree. Keyed off the value rather than the range because the
  // value is what the control has to render.
  if (typeof value === 'boolean') {
    return (
      <SettingToggle
        label={spec.label}
        description={spec.description}
        value={value}
        disabled={disabled}
        onCommit={(next) => onCommit(knobPatch(spec, next))}
      />
    )
  }

  return (
    <SettingSlider
      label={spec.label}
      lowLabel={spec.lowLabel}
      highLabel={spec.highLabel}
      description={spec.description}
      value={value}
      bounds={range ?? null}
      step={spec.step}
      decimals={spec.decimals}
      disabled={disabled}
      onCommit={(next) => onCommit(knobPatch(spec, next))}
    />
  )
}

/**
 * One group of settings, built from its declaration in `lib/settings.ts`.
 *
 * Knows nothing about which knobs exist — that list lives in one place so a knob cannot reach the
 * wire and then quietly have no control. Ranges come from `bounds`, never from here.
 *
 * A group whose knobs are all `advanced` collapses. That is a property of the data rather than a
 * flag passed in, so moving a knob out of Advanced is a one-line edit in the declarations.
 */
export function SettingsSection({
  group,
  settings,
  bounds,
  disabled,
  onCommit,
  children,
}: SettingsSectionProps) {
  const collapsible = group.knobs.length > 0 && group.knobs.every((knob) => knob.advanced)

  const body = (
    <div className="flex flex-col gap-4">
      {children}
      {group.knobs.map((spec) => (
        <Knob
          key={`${spec.section}.${spec.knob}`}
          spec={spec}
          settings={settings}
          bounds={bounds}
          disabled={disabled}
          onCommit={onCommit}
        />
      ))}
    </div>
  )

  if (collapsible) {
    return (
      <details className="surface p-4">
        <summary className="cursor-pointer text-sm font-semibold text-foreground">
          {group.title}
        </summary>
        <p className="mt-1 mb-4 text-xs text-muted-foreground">{group.summary}</p>
        {body}
      </details>
    )
  }

  return (
    <section className="surface flex flex-col gap-4 p-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">{group.title}</h2>
        <p className="mt-1 text-xs text-muted-foreground">{group.summary}</p>
      </div>
      {body}
    </section>
  )
}
