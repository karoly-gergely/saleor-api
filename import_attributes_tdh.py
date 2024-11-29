from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import numpy as np
import requests
import json
from tqdm import tqdm
from re import sub


def slugify(s):
    s = s.lower().strip()
    s = sub(r'[^\w\s-]', '', s)
    s = sub(r'[\s_-]+', '-', s)
    s = sub(r'^-+|-+$', '', s)

    return s


file_details = [
    ('./fixtures/attributes_excelport_en-gb__outdoor.terrisdraheim.com_2024-11-18_09-58-36_0.xlsx', "RICH_TEXT"),
    ('./fixtures/filters_excelport_en-gb__outdoor.terrisdraheim.com_2024-11-18_09-58-43_0.xlsx', "BOOLEAN"),
    ('./fixtures/options_excelport_en-gb__outdoor.terrisdraheim.com_2024-11-18_09-58-24_0.xlsx', "DROPDOWN")
]

graphql_endpoint = "https://admin.terrisdraheim.com/graphql/"


def get_auth_token():
    auth_url = graphql_endpoint
    auth_mutation = """
    mutation {
        tokenCreate(email: "karoly.gergely@spiderlinked.com", password: "u741363822.,U") {
            token
            errors {
                field
                message
            }
        }
    }
    """
    response = requests.post(auth_url, headers={"Content-Type": "application/json"}, json={"query": auth_mutation})
    response_data = response.json()
    if response_data.get('data', {}).get('tokenCreate', {}).get('token'):
        return response_data['data']['tokenCreate']['token']
    else:
        raise Exception("Authentication failed. Please check the credentials.")


auth_token = get_auth_token()


headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {auth_token}"
}


def process_file(file_path, input_type):
    items = []
    first_word = file_path.split('/')[-1].split('_')[0]
    excel_data = pd.ExcelFile(file_path)
    for sheet_name in excel_data.sheet_names:
        sheet_df = pd.read_excel(file_path, sheet_name=sheet_name)
        name_columns = [col for col in sheet_df.columns if 'name' in col.lower() and
                        'value' not in col.lower()]
        value_id_col = next((col for col in sheet_df.columns if
                             'value' in col.lower() and 'id' in col.lower()), None)
        value_name_col = next((col for col in sheet_df.columns if
                               'value' in col.lower() and 'name' in col.lower()), None)
        value_image_col = next((col for col in sheet_df.columns if
                               'value' in col.lower() and 'image' in col.lower()), None)

        if name_columns:
            for name_col in name_columns:
                # names = sheet_df[name_col].dropna().unique()
                names = sheet_df[name_col].dropna()
                columns = [name_col, value_id_col, value_name_col,
                           value_image_col] if value_image_col else ([name_col,
                                                                     value_id_col,
                                                                     value_name_col] if (value_id_col and value_name_col) else [name_col])
                row_generator = sheet_df[columns].iterrows()
                skipped_first = None
                for i, name in enumerate(names.values):
                    was_name_marked = False
                    external_reference = f"{first_word}-{sheet_df.iloc[names.index[i], 0]}-tdh-old"
                    if value_id_col and value_name_col:
                        values = []
                        for _, row in row_generator:
                            if skipped_first:
                                if skipped_first[value_name_col] is not np.nan:
                                    was_name_marked = True
                                    external_val_reference = f"values-{skipped_first[value_id_col]}-tdh-old"
                                    values.append(
                                        {'externalReference': external_val_reference,
                                         'name': skipped_first[value_name_col]}
                                    )
                                    if value_image_col and (skipped_first[value_image_col] is not np.nan):
                                        values[-1]['file'] = {
                                            'url': skipped_first[value_image_col],
                                        }
                                skipped_first = None

                            if (row[name_col] is np.nan) or row[name_col] == name:
                                if was_name_marked and row[name_col] == name:
                                    skipped_first = row.to_dict()
                                    break

                                if not was_name_marked and row[name_col] == name:
                                    was_name_marked = True
                                if row[value_name_col] is not np.nan:
                                    external_val_reference = f"values-{row[value_id_col]}-tdh-old"
                                    values.append(
                                        {'externalReference': external_val_reference,
                                         'name': row[value_name_col]}
                                    )
                                    if value_image_col and (row[value_image_col] is not np.nan):
                                        values[-1]['file'] = {
                                            'url': row[value_image_col],
                                        }
                            else:
                                skipped_first = row.to_dict()
                                break
                    else:
                        values = None
                    item = {
                        'name': name,
                        'externalReference': external_reference,
                        'inputType': input_type,
                        'slug': slugify(name) + f"-{sheet_df.iloc[names.index[i], 0]}",
                    }
                    if values:
                        item['values'] = values

                    items.append(item)

    return items


def send_data_to_saleor(items):
    mutation = """
    mutation bulkAttributeCreate(
    $input: [AttributeBulkCreateInput!]!
    $errorPolicy: ErrorPolicyEnum
    ) {
        attributeBulkCreate(attributes: $attributes, errorPolicy: $errorPolicy) {
            errors {
                path
                message
            }
            attribute {
                id
                name
                externalReference
                slug
                values {
                    name
                }
            }
        }
    }
    """
    variables = {
        "attributes": [
            {
                "name": item['name'],
                "externalReference": item['externalReference'],
                "inputType": item['inputType'],
                # "values": item['values']
                "values": [{"name": value['name']} for value in item['values']]
            } if item.get('values') else {
                "name": item['name'],
                "externalReference": item['externalReference'],
                "inputType": item['inputType']
            }
            for item in items
        ],
        "errorPolicy": "REJECT_EVERYTHING",
    }
    # response = requests.post(
    #     graphql_endpoint,
    #     headers=headers,
    #     json={'query': mutation, 'variables': variables}
    # )
    # return response.json()
    return None


all_items = []
with ThreadPoolExecutor() as executor:
    futures = [executor.submit(process_file, file_path, input_type) for file_path, input_type in file_details]
    for future in tqdm(futures, desc="Processing files"):
        all_items.extend(future.result())


response = send_data_to_saleor(all_items)
print(json.dumps(response, indent=4))


with open('./fixtures/attributes.json', 'w') as f:
    json.dump(all_items, f, indent=4)
