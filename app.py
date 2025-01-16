import os
import tempfile
from flask import Flask, request, jsonify
from basic_pitch.inference import predict_and_save
from supabase import create_client, Client
from werkzeug.utils import secure_filename
from functools import wraps
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

# Initialize Supabase
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# Add rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# File validation decorator
def validate_file(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
            
        # Check file type
        allowed_extensions = {'mp3', 'wav', 'ogg', 'flac', 'm4a'}
        if not '.' in file.filename or \
           file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
            return jsonify({'error': 'Invalid file type'}), 400
            
        # Check file size (e.g., 10MB limit)
        if len(file.read()) > 10 * 1024 * 1024:
            return jsonify({'error': 'File too large'}), 400
        file.seek(0)  # Reset file pointer
        
        return f(*args, **kwargs)
    return decorated_function

@app.route('/transcribe', methods=['POST'])
@limiter.limit("10 per minute")  # Rate limiting
@validate_file  # File validation
def transcribe_audio():
    try:
        # Create temporary directories for input and output
        with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
            # Save uploaded file to temporary input directory
            input_path = os.path.join(input_dir, secure_filename(request.files['file'].filename))
            request.files['file'].save(input_path)

            # Run Basic Pitch prediction
            predict_and_save(
                audio_path_list=[input_path],
                output_directory=output_dir,
                save_midi=True,
                sonify_midi=True,
                save_model_outputs=True,
                save_notes=True,
                # Add any other parameters you need
            )

            # Get the generated files
            output_files = {}
            for filename in os.listdir(output_dir):
                if filename.endswith(('.mid', '.wav', '.npz', '.csv')):
                    file_path = os.path.join(output_dir, filename)
                    
                    # Upload to Supabase Storage
                    with open(file_path, 'rb') as f:
                        file_data = f.read()
                        storage_path = f"transcriptions/{filename}"
                        supabase.storage.from_('your-bucket-name').upload(
                            storage_path,
                            file_data
                        )
                        
                        # Get public URL
                        file_url = supabase.storage.from_('your-bucket-name').get_public_url(storage_path)
                        output_files[filename] = file_url

            # Store metadata in Supabase database
            data = {
                'original_filename': request.files['file'].filename,
                'files': output_files,
                'status': 'completed',
                # Add any other metadata you want to store
            }
            
            result = supabase.table('transcriptions').insert(data).execute()

            return jsonify({
                'message': 'Transcription completed',
                'files': output_files,
                'metadata': result.data
            })

    except Exception as e:
        # Log the error properly in production
        print(f"Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True) 