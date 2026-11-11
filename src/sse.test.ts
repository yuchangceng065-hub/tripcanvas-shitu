import { afterEach, describe, it, expect, vi } from "vitest";
import { parseSSEBlock, streamRun, watchRun } from "./api";
const response = (text: string) =>
  new Response(new TextEncoder().encode(text), {
    headers: { "Content-Type": "text/event-stream" },
  });
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe("Agent SSE 与同一任务续接", () => {
  it("兼容旧事件与新状态封装，错误状态不会丢失", () => {
    expect(
      parseSSEBlock(
        'id: 3\nevent: tool_call\ndata: {"step":2,"tool":"route_day"}',
      ),
    ).toMatchObject({
      seq: 3,
      phase: "tool_call",
      detail: { step: 2, tool: "route_day" },
    });
    expect(
      parseSSEBlock(
        'id: 4\nevent: tool_result\ndata: {"status":"error","detail":{"error":"无路线"}}',
      ),
    ).toMatchObject({ status: "error", detail: { error: "无路线" } });
  });
  it("忽略心跳；不静默接受损坏的 JSON 或序号", () => {
    expect(parseSSEBlock(": keep-alive")).toBeNull();
    expect(() => parseSSEBlock("id: 1\nevent: model\ndata: {broken")).toThrow();
    expect(() =>
      parseSSEBlock("id: invalid\nevent: model\ndata: {}"),
    ).toThrow();
  });
  it("解析跨分片 CRLF 和 UTF-8，携带游标", async () => {
    const bytes = new TextEncoder().encode(
      'id: 4\r\nevent: tool_result\r\ndata: {"status":"error","detail":{"error":"暂不可达"}}\r\n\r\n',
    );
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            for (let i = 0; i < bytes.length; i += 3)
              controller.enqueue(bytes.slice(i, i + 3));
            controller.close();
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const received: unknown[] = [];
    await streamRun("r1", (event) => received.push(event), undefined, 3);
    expect(received).toEqual([
      expect.objectContaining({
        seq: 4,
        status: "error",
        detail: { error: "暂不可达" },
      }),
    ]);
    expect(fetchMock.mock.calls[0]![1].headers["Last-Event-ID"]).toBe("3");
  });
  it("流结束但后台仍运行时续接；补取、去重且不重复启动", async () => {
    vi.useFakeTimers();
    const event = { seq: 1, phase: "model", status: "ok", detail: { step: 1 } };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response('id: 1\nevent: model\ndata: {"step":1}\n\n'),
      )
      .mockResolvedValueOnce(json({ status: "running", events: [event] }))
      .mockResolvedValueOnce(
        response(
          'id: 1\nevent: model\ndata: {"step":1}\n\nid: 2\nevent: completed\ndata: {}\n\n',
        ),
      )
      .mockResolvedValueOnce(
        json({
          status: "completed",
          validation: {},
          events: [
            event,
            { seq: 2, phase: "completed", status: "ok", detail: {} },
          ],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const events: unknown[] = [];
    const pending = watchRun("r1", (e) => events.push(e));
    await vi.advanceTimersByTimeAsync(1100);
    expect((await pending).status).toBe("completed");
    expect(events).toHaveLength(2);
    expect(fetchMock.mock.calls[2]![1].headers["Last-Event-ID"]).toBe("1");
    expect(
      fetchMock.mock.calls.every(
        (args) => !String(args[0]).includes("/planner/agent"),
      ),
    ).toBe(true);
  });
  it("断网后通过持久化结果恢复遗漏事件", async () => {
    const event = { seq: 2, phase: "completed", status: "ok", detail: {} };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValueOnce(new TypeError("network"))
        .mockResolvedValueOnce(json({ status: "completed", events: [event] })),
    );
    const received: unknown[] = [];
    const run = await watchRun("r1", (e) => received.push(e), undefined, 1);
    expect(run.status).toBe("completed");
    expect(received).toEqual([event]);
  });
  it("取消查看后不会继续重连", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(watchRun("r1", () => {}, controller.signal)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
