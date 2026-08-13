"""Thư viện mạng nơ-ron thuần numpy (forward + backward), không cần torch."""
import numpy as np

def relu(x):
    return np.maximum(0.0, x)

def relu_grad(x):
    return (x > 0).astype(np.float64)

def softmax(x, axis=-1):
    z = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(z)
    return e / np.sum(e, axis=axis, keepdims=True)


class Net:
    """
    MLP nhỏ, 2 đầu ra (multi-task):
      - cls: softmax 5 lớp (vị trí khung oy = 0..4)
      - reg: 1 giá trị center-y (0..18) qua sigmoid*18
    """
    def __init__(self, in_dim, hidden=(64, 32), n_cls=5, seed=0):
        rng = np.random.default_rng(seed)
        self.in_dim = in_dim
        self.n_cls = n_cls
        sizes = [in_dim] + list(hidden)
        # trọng số + bias cho phần body (shared)
        self.W = []
        self.b = []
        for i in range(len(sizes) - 1):
            fan_in = sizes[i]
            # He init cho ReLU
            w = rng.normal(0, np.sqrt(2.0 / fan_in), size=(sizes[i], sizes[i+1]))
            b = np.zeros(sizes[i+1])
            self.W.append(w)
            self.b.append(b)
        # head cls
        last = sizes[-1]
        self.W_cls = rng.normal(0, 0.1, size=(last, n_cls))
        self.b_cls = np.zeros(n_cls)
        # head reg
        self.W_reg = rng.normal(0, 0.1, size=(last, 1))
        self.b_reg = np.zeros(1)

    def forward(self, X):
        """X: (N, in_dim). Trả dict chứa logits, y_cls (softmax), y_reg (0..18), cache."""
        zs = []
        acts = [X]
        a = X
        for w, b in zip(self.W, self.b):
            z = a @ w + b
            zs.append(z)
            a = relu(z)
            acts.append(a)
        # cls logits
        logits = a @ self.W_cls + self.b_cls
        # reg (0..18)
        reg_raw = a @ self.W_reg + self.b_reg
        y_reg = 18.0 * (1.0 / (1.0 + np.exp(-reg_raw)))  # sigmoid -> 0..18
        y_cls = softmax(logits, axis=1)
        cache = (zs, acts, logits, reg_raw)
        return {"cls": y_cls, "reg": y_reg[:, 0], "cache": cache}

    def predict(self, X):
        out = self.forward(X)
        oy = np.argmax(out["cls"], axis=1)
        return oy, out["reg"], out["cls"]

    def loss(self, X, oy_true, cy_true):
        out = self.forward(X)
        N = X.shape[0]
        # cross entropy
        logits = out["cache"][2]
        z = logits - np.max(logits, axis=1, keepdims=True)
        logsum = np.log(np.sum(np.exp(z), axis=1, keepdims=True))
        ce = -(z - logsum)[np.arange(N), oy_true]
        ce = np.mean(ce)
        # mse reg (scale về 0..18)
        mse = np.mean((out["reg"] - cy_true) ** 2)
        return {"ce": ce, "mse": mse, "total": ce + 0.05 * mse, "out": out}

    def grad(self, X, oy_true, cy_true):
        """Đạo hàm tổng loss = ce + 0.05*mse. Trả gradient cho mọi tham số."""
        N = X.shape[0]
        out = self.forward(X)
        zs, acts, logits, reg_raw = out["cache"]
        # d loss wrt logits -> y_cls
        y_cls = out["cls"]
        d_logits = y_cls.copy()
        d_logits[np.arange(N), oy_true] -= 1.0
        d_logits /= N
        # d loss wrt reg_raw (mse * 0.05)
        y_reg = out["reg"][:, None]
        err = (y_reg - cy_true[:, None])  # (N,1)
        # dy_reg/dreg_raw = 18*sigmoid*(1-sigmoid)
        sig = 1.0 / (1.0 + np.exp(-reg_raw))
        dsig = 18.0 * sig * (1.0 - sig)
        d_reg_raw = (2.0 * err / N) * 0.05 * dsig

        a_last = acts[-1]
        # grad wrt body last layer
        da = d_logits @ self.W_cls.T + d_reg_raw @ self.W_reg.T
        gW = []
        gb = []
        for i in reversed(range(len(self.W))):
            z = zs[i]
            a_in = acts[i]  # input to layer i (acts[0]=X)
            dz = da * relu_grad(z)
            gW.append(a_in.T @ dz)
            gb.append(np.sum(dz, axis=0))
            da = dz @ self.W[i].T
        gW = gW[::-1]
        gb = gb[::-1]
        gW_cls = a_last.T @ d_logits
        gb_cls = np.sum(d_logits, axis=0)
        gW_reg = a_last.T @ d_reg_raw
        gb_reg = np.sum(d_reg_raw, axis=0)
        return {
            "W": gW, "b": gb,
            "W_cls": gW_cls, "b_cls": gb_cls,
            "W_reg": gW_reg, "b_reg": gb_reg,
        }

    def step(self, grads, lr):
        for i in range(len(self.W)):
            self.W[i] -= lr * grads["W"][i]
            self.b[i] -= lr * grads["b"][i]
        self.W_cls -= lr * grads["W_cls"]
        self.b_cls -= lr * grads["b_cls"]
        self.W_reg -= lr * grads["W_reg"]
        self.b_reg -= lr * grads["b_reg"]

    def dump(self, path):
        d = {"n_cls": self.n_cls, "in_dim": self.in_dim}
        for i in range(len(self.W)):
            d[f"W{i}"] = self.W[i]
            d[f"b{i}"] = self.b[i]
        d["W_cls"] = self.W_cls
        d["b_cls"] = self.b_cls
        d["W_reg"] = self.W_reg
        d["b_reg"] = self.b_reg
        d["n_layers"] = len(self.W)
        np.savez(path, **d)

    @staticmethod
    def load(path):
        d = np.load(path)
        net = Net.__new__(Net)
        net.in_dim = int(d["in_dim"])
        net.n_cls = int(d["n_cls"])
        n = int(d["n_layers"])
        net.W = [d[f"W{i}"] for i in range(n)]
        net.b = [d[f"b{i}"] for i in range(n)]
        net.W_cls = d["W_cls"]
        net.b_cls = d["b_cls"]
        net.W_reg = d["W_reg"]
        net.b_reg = d["b_reg"]
        return net
