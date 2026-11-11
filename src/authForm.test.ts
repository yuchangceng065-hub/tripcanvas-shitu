import { describe, it, expect } from "vitest";
import { credentialsFromForm } from "./authForm";
import { apiErrorMessage } from "./apiError";
function form(username: string, password: string) {
  const data = new FormData();
  data.set("username", username);
  data.set("password", password);
  return data;
}
describe("账号提交与实际后端校验", () => {
  it("读取实际提交字段，兼容浏览器自动填充并保留密码空格", () =>
    expect(credentialsFromForm(form("  测试_123 ", "  password9 "))).toEqual({
      username: "测试_123",
      password: "  password9 ",
    }));
  it("七位密码直接给出明确提示，不进入网络请求", () =>
    expect(() => credentialsFromForm(form("3404233", "1234567"))).toThrow(
      "8—128",
    ));
  it("用户名规则和服务端一致，拒绝空白及非法符号", () => {
    expect(() => credentialsFromForm(form("a b", "password99"))).toThrow(
      "用户名",
    );
    expect(() => credentialsFromForm(form("ab", "password99"))).toThrow("3—32");
  });
  it("FastAPI 422 错误数组显示字段原因，不回显密钥或密码", () => {
    const message = apiErrorMessage(
      [
        {
          loc: ["body", "password"],
          type: "string_too_short",
          input: "secret-value",
          msg: "String too short",
        },
      ],
      422,
    );
    expect(message).toContain("密码需要 8");
    expect(message).not.toContain("secret-value");
  });
  it("保留具体业务拒绝和限流原因", () => {
    expect(apiErrorMessage("模型服务暂不可用", 502)).toBe("模型服务暂不可用");
    expect(apiErrorMessage({}, 429)).toContain("请求较多");
  });
});
