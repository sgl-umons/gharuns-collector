from dotenv import load_dotenv

load_dotenv()     

INPUT_FILE = './data-raw/workflows.csv.zst'
# GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# if not GITHUB_TOKEN:
#     raise ValueError("GITHUB_TOKEN is missing! Check your .env file.")

GITHUB_TOKEN = None 


TEST_LIMIT = 10   
RETENTION_DAYS = 87
WINDOW_DAYS = 7
GRACE_PERIOD_DAYS = 7 
GRAPHQL_BATCH_SIZE = 10

GQL_JOBS_LIMIT = 35
GQL_STEPS_LIMIT = 26

OUTPUT_DIR = "data"
STATE_DIR = "states"
REPORT_DIR = "reports"

STATE_FILE =None

REPO_LOG_FILE = "repo_log.csv"
BATCH_LOG_FILE = "batch_log.csv"
RATE_LIMIT_LOG_FILE = "rate_limit_events.csv"
REDIRECT_LOG_FILE = "redirect_log.csv"