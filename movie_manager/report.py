import json
import os
from jinja2 import Environment, FileSystemLoader

def generate_report(files, groups, output_file):
    """
    Generates the HTML report using Jinja2 template.
    """
    env = Environment(
        loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), 'templates')),
        variable_start_string='[[[',
        variable_end_string=']]]'
    )
    template = env.get_template('report.html')
    
    # Convert data to JSON for embedding
    # Use ensure_ascii=False for readability and correct encoding handling
    files_json = json.dumps(files, default=str, ensure_ascii=False)
    groups_json = json.dumps(groups, default=str, ensure_ascii=False)
    
    html_content = template.render(
        files_json=files_json,
        groups_json=groups_json
    )
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
