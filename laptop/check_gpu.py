"""Verify real CUDA execution, not just that a driver is installed."""

import torch


def main():
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; a compatible NVIDIA driver is required")
    value = torch.ones(2, 2, device="cuda")
    result = (value @ value).cpu()
    assert torch.equal(result, torch.full((2, 2), 2.0))
    print("CUDA execution passed:", torch.cuda.get_device_name(), torch.__version__)


if __name__ == "__main__":
    main()
