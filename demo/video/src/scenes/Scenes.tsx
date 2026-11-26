/**
 * The eight scenes, in script order.
 *
 * Each is written to start at its own frame 0 and is placed on the timeline by
 * a <Sequence> in DriftCatchVideo, so a scene's internal timings read the same
 * as the beats in SCRIPT.md.
 */

import React from "react";
import { interpolate, useCurrentFrame } from "remotion";

import {
  Caption,
  Cursor,
  Scene,
  Trajectory,
  Window,
  useEnter,
  usePop,
  useTyped,
} from "../components";
import { color, font, sec, size } from "../theme";

// ------------------------------------------------ 1. the one-word change

export const SceneDiff: React.FC = () => {
  const frame = useCurrentFrame();
  const swap = frame > sec(1.6);

  return (
    <Scene>
      <Window title="prompts/refund_agent.txt" width={1180}>
        <div style={{ color: color.dim }}>You are a refund agent.</div>
        <div style={{ height: 10 }} />
        <DiffLine
          sign="-"
          text="Resolve the customer's issue."
          tone={color.drift}
          visible={swap}
          fading
        />
        <DiffLine
          sign="+"
          text="Resolve the customer's issue carefully."
          tone={color.pass}
          visible={swap}
          emphasise="carefully."
        />
      </Window>
      <Caption startFrame={sec(2.6)}>You changed one word.</Caption>
    </Scene>
  );
};

const DiffLine: React.FC<{
  sign: string;
  text: string;
  tone: string;
  visible: boolean;
  fading?: boolean;
  emphasise?: string;
}> = ({ sign, text, tone, visible, fading, emphasise }) => {
  const frame = useCurrentFrame();
  const progress = interpolate(frame, [sec(1.6), sec(2.1)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  if (!visible) return <div style={{ height: 50 }} />;

  const head = emphasise ? text.slice(0, text.length - emphasise.length) : text;

  return (
    <div
      style={{
        color: tone,
        opacity: fading ? 1 - progress * 0.45 : progress,
        background: fading ? "rgba(248,81,73,0.08)" : "rgba(63,185,80,0.10)",
        padding: "6px 12px",
        borderRadius: 6,
        marginBottom: 6,
        textDecoration: fading ? "line-through" : "none",
      }}
    >
      <span style={{ opacity: 0.7, marginRight: 14 }}>{sign}</span>
      {head}
      {emphasise && (
        <span
          style={{
            background: "rgba(63,185,80,0.28)",
            borderRadius: 4,
            padding: "2px 4px",
          }}
        >
          {emphasise}
        </span>
      )}
    </div>
  );
};

// --------------------------------------------- 2. what it silently did

export const SceneDamage: React.FC = () => (
  <Scene>
    <div style={{ display: "flex", flexDirection: "column", gap: 52 }}>
      <Row label="before" startFrame={sec(0.2)}>
        <Trajectory
          steps={["lookup_order", "check_eligibility", "issue_refund"]}
          startFrame={sec(0.3)}
          scale={1.18}
        />
      </Row>
      <Row label="after" startFrame={sec(1.9)}>
        <Trajectory
          steps={["lookup_order", "check_eligibility", "escalate_to_human"]}
          highlightLast="drift"
          startFrame={sec(2.0)}
          scale={1.18}
        />
      </Row>
    </div>

    <Caption startFrame={sec(3.6)} endFrame={sec(5.3)}>
      Your agent stopped refunding customers.
    </Caption>
    <Caption startFrame={sec(5.5)} tone="drift">
      Every test still passed.
    </Caption>
  </Scene>
);

const Row: React.FC<{
  label: string;
  startFrame: number;
  children: React.ReactNode;
}> = ({ label, startFrame, children }) => {
  const enter = useEnter(startFrame);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 30, ...enter }}>
      <span
        style={{
          width: 120,
          textAlign: "right",
          color: color.dim,
          font: `600 26px ${font.sans}`,
          letterSpacing: 1.4,
          textTransform: "uppercase",
        }}
      >
        {label}
      </span>
      {children}
    </div>
  );
};

// ------------------------------------------------------------ 3. thesis

export const SceneThesis: React.FC = () => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, sec(0.9)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <Scene captionSpace={0}>
      <div
        style={{
          color: color.text,
          font: `800 ${size.thesis}px ${font.sans}`,
          letterSpacing: -2.4,
          textAlign: "center",
          lineHeight: 1.18,
          opacity,
        }}
      >
        Agents don&rsquo;t fail loudly.
        <br />
        They{" "}
        <span style={{ color: color.drift }}>drift</span>.
      </div>
    </Scene>
  );
};

// ------------------------------------------------------ 4. product reveal

export const SceneReveal: React.FC = () => {
  const pop = usePop(sec(0.1));
  const sub = useEnter(sec(0.8));
  return (
    <Scene>
      <div style={{ textAlign: "center" }}>
        <div
          style={{
            font: `800 ${size.wordmark}px ${font.sans}`,
            letterSpacing: -3.6,
            color: color.text,
            ...pop,
          }}
        >
          Drift<span style={{ color: color.accent }}>Catch</span>
        </div>
        <div
          style={{
            marginTop: 20,
            color: color.dim,
            font: `500 42px ${font.sans}`,
            letterSpacing: -0.6,
            ...sub,
          }}
        >
          Catch the moment your agent&rsquo;s behaviour changes.
        </div>
      </div>
      <Caption startFrame={sec(2.2)} tone="accent">
        A pre-merge CI gate for agent behaviour.
      </Caption>
    </Scene>
  );
};

// ----------------------------------------------------------- 5. record

const RECORD_CODE = `import driftcatch as drift

def test_refund():
    with drift.take("refund_1182") as take:
        agent("refund order 1182")`;

export const SceneRecord: React.FC = () => {
  const { shown, done } = useTyped(RECORD_CODE, sec(0.3), 3.1);
  const termEnter = useEnter(sec(4.6));
  const frame = useCurrentFrame();

  return (
    <Scene>
      <div style={{ display: "flex", flexDirection: "column", gap: 26, alignItems: "center" }}>
        <Window title="tests/test_refund.py" width={1100}>
          <pre style={{ margin: 0, color: color.text, whiteSpace: "pre-wrap" }}>
            <Code text={shown} />
            <Cursor visible={!done} />
          </pre>
        </Window>

        <div style={{ ...termEnter }}>
          <Window title="terminal" width={1100}>
            <Line visible={frame > sec(4.8)}>
              <Prompt />
              pytest
            </Line>
            <Line visible={frame > sec(5.4)} tone={color.warn}>
              recorded refund_1182 · PENDING
            </Line>
            <Line visible={frame > sec(6.4)}>
              <Prompt />
              drift approve refund_1182 --by you
            </Line>
            <Line visible={frame > sec(7.0)} tone={color.pass}>
              approved refund_1182
            </Line>
          </Window>
        </div>
      </div>

      <Caption startFrame={sec(1.4)} endFrame={sec(6.4)}>
        Record one real run.
      </Caption>
      <Caption startFrame={sec(6.6)} tone="accent">
        A human approves it. Recording isn&rsquo;t approving.
      </Caption>
    </Scene>
  );
};

// -------------------------------------------------- 6. replay and catch

export const SceneCatch: React.FC = () => {
  const frame = useCurrentFrame();
  // Width in pixels, drawn left to right under the diverging step.
  const underline = interpolate(frame, [sec(6.4), sec(7.3)], [0, 340], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <Scene>
      <Window title="terminal" width={1500}>
        <Line visible={frame > sec(0.3)}>
          <Prompt />
          pytest
        </Line>
        <Line visible={frame > sec(0.9)} tone={color.pass}>
          1 passed
          <span style={{ color: color.dim, marginLeft: 24 }}>
            replayed from disk · no API calls · $0.00
          </span>
        </Line>

        <div style={{ height: 22 }} />

        <Line visible={frame > sec(2.6)} tone={color.dim}>
          # someone changes one word in the prompt
        </Line>
        <Line visible={frame > sec(3.1)}>
          <Prompt />
          pytest
        </Line>
        <Line visible={frame > sec(3.7)} tone={color.drift}>
          FAILED test_refund.py
          <span style={{ color: color.dim, marginLeft: 24 }}>behaviour changed</span>
        </Line>

        <div style={{ height: 22 }} />

        <Line visible={frame > sec(4.6)}>
          <Prompt />
          drift bisect refund_1182 --previous
        </Line>
        <Line visible={frame > sec(5.3)}>
          <span style={{ color: color.dim }}>{"  golden:     "}</span>
          <span style={{ color: color.pass }}>
            lookup_order → check_eligibility → issue_refund
          </span>
        </Line>
        <Line visible={frame > sec(5.9)}>
          <span style={{ color: color.dim }}>{"  candidate:  "}</span>
          <span style={{ color: color.text }}>lookup_order → check_eligibility → </span>
          <span style={{ color: color.drift }}>escalate_to_human</span>
        </Line>
        <div
          style={{
            display: "flex",
            minHeight: "0.5em",
            // Without this the spacer's repeated spaces collapse and the rule
            // starts several characters left of the word it underlines.
            whiteSpace: "pre",
          }}
        >
          <span style={{ visibility: "hidden" }}>
            {"  candidate:  lookup_order → check_eligibility → "}
          </span>
          <span
            style={{
              display: "inline-block",
              height: 5,
              borderRadius: 3,
              marginTop: 6,
              background: color.drift,
              width: underline,
            }}
          />
        </div>

        <div style={{ height: 22 }} />

        <Line visible={frame > sec(7.4)}>
          <span
            style={{
              color: color.warn,
              background: "rgba(210,153,34,0.16)",
              padding: "3px 10px",
              borderRadius: 4,
            }}
          >
            ROOT CAUSE: the system prompt
          </span>
        </Line>
        <Line visible={frame > sec(8.0)} tone={color.drift}>
          {"    - issue."}
        </Line>
        <Line visible={frame > sec(8.4)} tone={color.pass}>
          {"    + issue carefully."}
        </Line>
      </Window>

      <Caption startFrame={sec(1.3)} tone="pass" endFrame={sec(2.4)}>
        Replay forever. Free, offline, deterministic.
      </Caption>
      <Caption startFrame={sec(8.8)} tone="accent">
        Caught before merge &mdash; and it names the cause.
      </Caption>
    </Scene>
  );
};

// ------------------------------------------- 7. rules you never wrote

const RULES: Array<{ text: string; evidence: string; star?: boolean }> = [
  { text: "lookup_order always runs before issue_refund", evidence: "23/23" },
  { text: "validate_customer is called in every run", evidence: "23/23" },
  { text: "issue_refund is always the last tool", evidence: "18/18" },
  {
    text: "lookup_order.order_id always equals issue_refund.order_id",
    evidence: "18/18",
    star: true,
  },
];

export const SceneLearn: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Scene>
      <Window title="terminal" width={1400}>
        <Line visible={frame > sec(0.2)}>
          <Prompt />
          drift learn
        </Line>
        <Line visible={frame > sec(0.9)} tone={color.dim}>
          Mined 15 rules from 23 approved recordings
        </Line>
        <div style={{ height: 22 }} />
        {RULES.map((rule, index) => (
          <Rule
            key={rule.text}
            rule={rule}
            startFrame={sec(1.6 + index * 1.15)}
          />
        ))}
      </Window>
      <Caption startFrame={sec(6.6)} tone="accent">
        Nobody wrote these. They were learned from runs you approved.
      </Caption>
    </Scene>
  );
};

const Rule: React.FC<{
  rule: { text: string; evidence: string; star?: boolean };
  startFrame: number;
}> = ({ rule, startFrame }) => {
  const enter = useEnter(startFrame, 10);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: rule.star ? "10px 14px" : "6px 14px",
        marginBottom: 6,
        borderRadius: 8,
        background: rule.star ? "rgba(77,159,255,0.12)" : "transparent",
        border: `1px solid ${rule.star ? "rgba(77,159,255,0.35)" : "transparent"}`,
        ...enter,
      }}
    >
      <span style={{ color: color.pass }}>✓</span>
      <span style={{ color: rule.star ? color.text : color.dim, flex: 1 }}>{rule.text}</span>
      <span style={{ color: color.dim, fontSize: 26 }}>{rule.evidence}</span>
    </div>
  );
};

// --------------------------------------------------------------- 8. CTA

export const SceneCTA: React.FC = () => {
  const pop = usePop(sec(0.1));
  const install = useEnter(sec(0.9));
  const link = useEnter(sec(1.4));
  const tag = useEnter(sec(2.0));

  return (
    <Scene captionSpace={0}>
      <div style={{ textAlign: "center" }}>
        <div
          style={{
            font: `800 ${size.wordmark}px ${font.sans}`,
            letterSpacing: -3.6,
            color: color.text,
            ...pop,
          }}
        >
          Drift<span style={{ color: color.accent }}>Catch</span>
        </div>

        <div
          style={{
            marginTop: 34,
            display: "inline-block",
            padding: "16px 34px",
            borderRadius: 10,
            background: color.surface,
            border: `1px solid ${color.line}`,
            color: color.text,
            font: `34px ${font.mono}`,
            ...install,
          }}
        >
          <span style={{ color: color.pass, marginRight: 14 }}>$</span>
          pip install driftcatch
        </div>

        <div
          style={{
            marginTop: 26,
            color: color.accent,
            font: `500 32px ${font.mono}`,
            ...link,
          }}
        >
          github.com/NEEL7127/driftcatch
        </div>

        <div
          style={{
            marginTop: 40,
            color: color.dim,
            font: `600 34px ${font.sans}`,
            letterSpacing: -0.4,
            ...tag,
          }}
        >
          Record once. Replay forever. Catch the drift.
        </div>
      </div>
    </Scene>
  );
};

// ------------------------------------------------------------- helpers

const Prompt: React.FC = () => (
  <span style={{ color: color.pass, marginRight: 14 }}>$</span>
);

const Line: React.FC<{
  children: React.ReactNode;
  visible: boolean;
  tone?: string;
}> = ({ children, visible, tone }) => (
  <div
    style={{
      color: tone ?? color.text,
      opacity: visible ? 1 : 0,
      minHeight: "1.62em",
      whiteSpace: "pre-wrap",
    }}
  >
    {visible ? children : null}
  </div>
);

/**
 * Minimal Python highlighting.
 *
 * Deliberately crude - a full tokeniser would be more code than the rest of
 * this file, and at 30px for four seconds the eye only needs keywords and
 * strings to separate from everything else.
 */
const Code: React.FC<{ text: string }> = ({ text }) => {
  const parts = text.split(/(\s+|[()".,:])/);
  const keywords = new Set(["import", "as", "def", "with", "return"]);
  let inString = false;

  return (
    <>
      {parts.map((part, index) => {
        if (part === '"') {
          inString = !inString;
          return (
            <span key={index} style={{ color: color.warn }}>
              {part}
            </span>
          );
        }
        if (inString) {
          return (
            <span key={index} style={{ color: color.warn }}>
              {part}
            </span>
          );
        }
        if (keywords.has(part)) {
          return (
            <span key={index} style={{ color: color.accent }}>
              {part}
            </span>
          );
        }
        if (part === "drift" || part === "driftcatch") {
          return (
            <span key={index} style={{ color: color.pass }}>
              {part}
            </span>
          );
        }
        return <span key={index}>{part}</span>;
      })}
    </>
  );
};
