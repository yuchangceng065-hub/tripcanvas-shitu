"""Railway supplies PORT; single worker keeps public routing throttling shared."""
import os
import uvicorn

if __name__ == '__main__':
    uvicorn.run('backend.app:app',host='0.0.0.0',port=int(os.environ.get('PORT','8000')),workers=1)
