import { describe, it, expect } from "vitest";
import { durationLabel } from "./planningOptions";

describe("方案指标时长显示", () => {
  it("不足一小时只显示分钟", () => expect(durationLabel(23)).toBe("23 分钟"));
  it("整小时不显示零分", () => expect(durationLabel(60)).toBe("1 小时"));
  it("小时与分钟", () => expect(durationLabel(87)).toBe("1 小时 27 分"));
  it("负数与小数安全", () => expect(durationLabel(-5)).toBe("0 分钟"));
});
