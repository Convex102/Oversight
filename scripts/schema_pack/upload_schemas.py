import argparse, base64, json, requests, glob, os

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--api", required=True, help="Base URL, e.g. http://localhost:8000")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--key", required=True, help="X-API-Key")
    args=ap.parse_args()

    headers={"X-API-Key": args.key}
    for fp in glob.glob("schemas/*.json"):
        schema=json.load(open(fp,"r",encoding="utf-8"))
        payload={
            "tenant_id": args.tenant,
            "schema_json_b64": base64.b64encode(json.dumps(schema).encode("utf-8")).decode("utf-8"),
            "schema_name": schema.get("name"),
            "schema_version": schema.get("version"),
        }
        r=requests.post(args.api.rstrip("/")+"/v1/schemas/upload", json=payload, headers=headers, timeout=60)
        print(fp, r.status_code, r.text)

if __name__=="__main__":
    main()
