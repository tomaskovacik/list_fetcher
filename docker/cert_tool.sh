#!/bin/sh
set -eu

OUTPUT_DIR="${LIST_FETCHER_CERT_OUTPUT_DIR:-/certs}"
CERT_NAME="${LIST_FETCHER_CERT_NAME:-sharepoint-app}"
COMMON_NAME="${LIST_FETCHER_CERT_COMMON_NAME:-list-fetcher}"
VALID_DAYS="${LIST_FETCHER_CERT_DAYS:-825}"
FORCE="${LIST_FETCHER_CERT_FORCE:-false}"

KEY_PATH="${OUTPUT_DIR}/${CERT_NAME}-key.pem"
CERT_PEM_PATH="${OUTPUT_DIR}/${CERT_NAME}-cert.pem"
CERT_DER_PATH="${OUTPUT_DIR}/${CERT_NAME}-cert.cer"
INFO_PATH="${OUTPUT_DIR}/${CERT_NAME}-cert-info.txt"

mkdir -p "${OUTPUT_DIR}"

if [ "${FORCE}" != "true" ] && { [ -f "${KEY_PATH}" ] || [ -f "${CERT_PEM_PATH}" ] || [ -f "${CERT_DER_PATH}" ]; }; then
  echo "error: certificate files already exist in ${OUTPUT_DIR}. Set LIST_FETCHER_CERT_FORCE=true to overwrite." >&2
  exit 1
fi

openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout "${KEY_PATH}" \
  -out "${CERT_PEM_PATH}" \
  -days "${VALID_DAYS}" \
  -subj "/CN=${COMMON_NAME}"

openssl x509 -in "${CERT_PEM_PATH}" -outform der -out "${CERT_DER_PATH}"
THUMBPRINT="$(openssl x509 -in "${CERT_PEM_PATH}" -noout -fingerprint -sha1 | cut -d= -f2 | tr -d ':')"
END_DATE="$(openssl x509 -in "${CERT_PEM_PATH}" -noout -enddate | cut -d= -f2-)"

cat > "${INFO_PATH}" <<EOF
Certificate generated successfully.

Files:
- Private key: ${KEY_PATH}
- Public certificate (PEM): ${CERT_PEM_PATH}
- Public certificate (DER/CER): ${CERT_DER_PATH}

Upload to Entra:
- Upload ${CERT_PEM_PATH} or ${CERT_DER_PATH} in App registrations -> Certificates & secrets -> Certificates

Thumbprint:
${THUMBPRINT}

Expires:
${END_DATE}

Suggested .env values:
SP_EXPORT_CERT_PATH=/config/${CERT_NAME}-key.pem
SP_EXPORT_CERT_THUMBPRINT=${THUMBPRINT}
EOF

printf 'Generated certificate material in %s\n' "${OUTPUT_DIR}"
printf 'Thumbprint: %s\n' "${THUMBPRINT}"
printf 'Upload this file to Entra: %s\n' "${CERT_PEM_PATH}"
