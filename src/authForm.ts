// Read submitted controls rather than relying on v-model after browser autofill.
export function credentialsFromForm(data: Pick<FormData, "get">) {
  const username = String(data.get("username") ?? "").trim();
  const password = String(data.get("password") ?? "");
  if (username.length < 3 || username.length > 32)
    throw new Error("用户名需要 3—32 个字符");
  if (!/^[\p{L}\p{N}_-]+$/u.test(username))
    throw new Error("用户名只能包含文字、数字、下划线或短横线");
  if (password.length < 8 || password.length > 128)
    throw new Error("密码需要 8—128 个字符，请检查后重试");
  return { username, password };
}
