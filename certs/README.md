# certs/

Place your three AWS IoT Core device certificate files here.

| Filename | Source |
|----------|--------|
| `device.pem.crt` | Downloaded when creating an IoT Thing |
| `private.pem.key` | Downloaded when creating an IoT Thing |
| `AmazonRootCA1.pem` | Downloaded from AWS or https://www.amazontrust.com/repository/AmazonRootCA1.pem |

All three files are gitignored. Never commit them.
See docs/aws_setup.md → Step 1 for full instructions.
