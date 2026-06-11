#!/usr/bin/env python3
"""
Creates a new UEM tenant by forging a SAML token signed with a self-generated
RSA key, replacing mycpscert in the DB with our cert, then POSTing to
POST https://localhost:8895/partition/tenant via besng-basic auth.
"""

import base64
import datetime
import subprocess
import sys
import uuid
import json
import os
import requests
import urllib3
from lxml import etree
from signxml import XMLSigner, methods
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.x509 import NameAttribute
from cryptography import x509

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TENANT_EXT_ID = "502BD069-76C3-4834-BEBE-D7F120BCF3EF"
SAML_USER_TOKEN = "502BD069-76C3-4834-BEBE-D7F120BCF3EF"
PARTITION_URL = "https://localhost:8895/partition/tenant"

def run_psql(sql):
    result = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "uem", "-c", sql],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"psql error: {result.stderr}", file=sys.stderr)
    return result.stdout

STABLE_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saml_keys", "saml_key.pem")
STABLE_CERT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saml_keys", "saml_cert.pem")

def generate_key_and_cert():
    if os.path.exists(STABLE_KEY_PATH) and os.path.exists(STABLE_CERT_PATH):
        print("[1] Loading stable SAML key/cert from saml_keys/ (reusing for DB consistency)...")
        with open(STABLE_KEY_PATH, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
        with open(STABLE_CERT_PATH, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read(), default_backend())
        return private_key, cert

    print("[1] Generating RSA-2048 key pair and self-signed cert...")
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    subject = issuer = x509.Name([
        x509.NameAttribute(x509.NameOID.COUNTRY_NAME, "CA"),
        x509.NameAttribute(x509.NameOID.STATE_OR_PROVINCE_NAME, "Ontario"),
        x509.NameAttribute(x509.NameOID.LOCALITY_NAME, "Waterloo"),
        x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, "Research In Motion"),
        x509.NameAttribute(x509.NameOID.ORGANIZATIONAL_UNIT_NAME, "Enterprise Software"),
        x509.NameAttribute(x509.NameOID.COMMON_NAME, "CPS Token Signing"),
    ])
    cert = (x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365*20))
        .sign(private_key, hashes.SHA256(), default_backend()))
    return private_key, cert

def update_mycpscert_in_db(cert):
    print("[2] Replacing mycpscert in DB...")
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    # Escape single quotes for SQL
    cert_pem_escaped = cert_pem.replace("'", "''")
    expiry = cert.not_valid_after_utc.strftime("%Y-%m-%d %H:%M:%S") if hasattr(cert, 'not_valid_after_utc') else (datetime.datetime.utcnow() + datetime.timedelta(days=365*20)).strftime("%Y-%m-%d %H:%M:%S")
    sql = f"UPDATE uem.obj_keystore_entry SET certificate='{cert_pem_escaped}', modified=now() WHERE alias='mycpscert' AND id_keystore=7;"
    out = run_psql(sql)
    print(f"    DB update result: {out.strip()}")

def build_saml_xml():
    print("[3] Building SAML XML response...")
    now = datetime.datetime.utcnow()
    not_before = (now - datetime.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    not_on_or_after = (now + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    issue_instant = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    response_id = "_" + uuid.uuid4().hex
    assertion_id = "_" + uuid.uuid4().hex

    SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
    SAML = "urn:oasis:names:tc:SAML:2.0:assertion"

    nsmap = {
        "samlp": SAMLP,
        "saml": SAML,
    }

    root = etree.Element(f"{{{SAMLP}}}Response", nsmap=nsmap)
    root.set("ID", response_id)
    root.set("Version", "2.0")
    root.set("IssueInstant", issue_instant)

    status = etree.SubElement(root, f"{{{SAMLP}}}Status")
    status_code = etree.SubElement(status, f"{{{SAMLP}}}StatusCode")
    status_code.set("Value", "urn:oasis:names:tc:SAML:2.0:status:Success")

    assertion = etree.SubElement(root, f"{{{SAML}}}Assertion")
    assertion.set("ID", assertion_id)
    assertion.set("Version", "2.0")
    assertion.set("IssueInstant", issue_instant)

    issuer = etree.SubElement(assertion, f"{{{SAML}}}Issuer")
    issuer.text = "CPSIssuer"

    conditions = etree.SubElement(assertion, f"{{{SAML}}}Conditions")
    conditions.set("NotBefore", not_before)
    conditions.set("NotOnOrAfter", not_on_or_after)

    attr_stmt = etree.SubElement(assertion, f"{{{SAML}}}AttributeStatement")

    def add_attr(parent, name, value):
        attr = etree.SubElement(parent, f"{{{SAML}}}Attribute")
        attr.set("Name", name)
        av = etree.SubElement(attr, f"{{{SAML}}}AttributeValue")
        av.text = value

    add_attr(attr_stmt, "TOKEN_EXTERNAL_ORGANIZATION_ID", TENANT_EXT_ID)
    add_attr(attr_stmt, "TOKEN_EXTERNAL_USER_ID", SAML_USER_TOKEN)
    add_attr(attr_stmt, "TOKEN_USER_DISPLAY_NAME", "Admin")
    add_attr(attr_stmt, "TOKEN_USER_NAME", "admin")

    return root, response_id

def sign_saml(root, private_key, cert):
    print("[4] Signing SAML XML with our private key...")
    # Get private key PEM
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    )

    # Sign the root (Response) element
    # signxml will look for ID/Id/id attribute and create reference to #ID_value
    signed = signer.sign(
        root,
        key=private_key_pem,
        cert=cert_pem,
    )
    return signed

def build_auth_header(saml_xml_bytes):
    print("[5] Building besng-basic Authorization header...")
    saml_b64 = base64.b64encode(saml_xml_bytes).decode()
    # Format: provider="saml" tenant="<ext_id>" username="<user>" credentials="<saml_b64>"
    # This matches BesngBasicAuthorizationHeaderValueGenerator.getKeyValueString format
    token_str = f'provider="saml" tenant="{TENANT_EXT_ID}" username="admin" credentials="{saml_b64}"'
    token_b64 = base64.b64encode(token_str.encode()).decode()
    auth_header = f"besng-basic {token_b64}"
    return auth_header

def create_tenant(auth_header, new_tenant_name, new_tenant_ext_id, admin_password, auth_key=None, srp_host=None):
    print("[6] POSTing to /partition/tenant...")
    effective_auth_key = auth_key if auth_key else str(uuid.uuid4())
    effective_srp_host = srp_host if srp_host else "uemlinux"
    payload = {
        "partition.tenant.name": new_tenant_name,
        "partition.tenant.orgid": new_tenant_ext_id,
        "partition.tenant.authkey": effective_auth_key,
        "partition.tenant.proxyuser.externalauthid": str(uuid.uuid4()).upper(),
        "partition.tenant.commonname": new_tenant_name,
        "partition.tenant.organizationunit": "IT",
        "partition.tenant.cityname": "Waterloo",
        "partition.tenant.statename": "Ontario",
        "partition.tenant.countrycode": "CA",
        "partition.tenant.contactname": "Admin",
        "partition.tenant.telephonenumber": "5191234567",
        "partition.tenant.contactemail": "admin@example.com",
        "partition.tenant.adminpassword": admin_password,
        "partition.tenant.onprem": True,
        "partition.srp.host": effective_srp_host,
        "partition.tenant.customDomain": "",
        "partition.tenant.servicename": "MDM",
        "partition.tenant.servicecapability": "MDM",
        "partition.tenant.adminsource": "LOCAL",
        "partition.tenant.resetAdminPassword": False,
        "partition.tenant.suppresswelcomeemail": True,
        "partition.tenant.serviceset": [],
    }
    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
    }
    response = requests.post(
        PARTITION_URL,
        json=payload,
        headers=headers,
        verify=False,
        timeout=120,
    )
    print(f"    HTTP {response.status_code}")
    print(f"    Response: {response.text[:500]}")
    return response

def main():
    # Args: name extId password [authkey] [srp_host]
    new_tenant_name = sys.argv[1] if len(sys.argv) > 1 else "tenant.1"
    new_tenant_ext_id = sys.argv[2] if len(sys.argv) > 2 else str(uuid.uuid4()).upper()
    admin_password = sys.argv[3] if len(sys.argv) > 3 else "Password1!"
    auth_key = sys.argv[4] if len(sys.argv) > 4 else None
    srp_host = sys.argv[5] if len(sys.argv) > 5 else None

    print(f"Creating tenant: name={new_tenant_name}, extId={new_tenant_ext_id}")

    private_key, cert = generate_key_and_cert()
    update_mycpscert_in_db(cert)
    root, response_id = build_saml_xml()
    signed = sign_saml(root, private_key, cert)
    saml_xml_bytes = etree.tostring(signed, pretty_print=False)
    auth_header = build_auth_header(saml_xml_bytes)
    response = create_tenant(auth_header, new_tenant_name, new_tenant_ext_id, admin_password, auth_key, srp_host)

    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    logdir = f"/home/uem/uem/lab/CoreUILinux/logs/{today}"
    logfile = sorted([f for f in os.listdir(logdir) if f.startswith("UEMLINUX_CORE")], reverse=True)[0] if os.path.isdir(logdir) else None

    if response.status_code in (200, 201):
        print(f"\n[SUCCESS] Tenant '{new_tenant_name}' created with extId={new_tenant_ext_id}")
    else:
        print(f"\n[FAILED] HTTP {response.status_code}")
        if logfile:
            print(f"\nRecent Core log entries (last 30 lines of {logfile}):")
            subprocess.run(["tail", "-30", os.path.join(logdir, logfile)])

if __name__ == "__main__":
    main()
