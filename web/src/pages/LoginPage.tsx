import { type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import { useLogin, useMe } from "@/api/hooks";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { safeNext } from "@/lib/filters";

export function LoginPage() {
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const navigate = useNavigate();
  const me = useMe();
  const login = useLogin();

  if (me.data) return <Navigate to={next} replace />;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    login.mutate(
      { email: String(data.get("email") ?? ""), password: String(data.get("password") ?? "") },
      { onSuccess: () => navigate(next, { replace: true }) },
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>PM Mail Assistant</CardTitle>
          <p className="text-sm text-muted-foreground">Đăng nhập để xem mail đã đồng bộ.</p>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="grid gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" autoComplete="username" required />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="password">Mật khẩu</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </div>
            {login.error && (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(login.error)}</AlertDescription>
              </Alert>
            )}
            <Button type="submit" disabled={login.isPending}>
              {login.isPending ? "Đang đăng nhập…" : "Đăng nhập"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
