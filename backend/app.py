"""
The flask application for our program
"""
# importing required python libraries
from flask import Flask, jsonify, request, send_file, redirect, url_for, session
from flask_mongoengine import MongoEngine
from markupsafe import escape
from flask_cors import CORS, cross_origin
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from itertools import islice
from webdriver_manager.chrome import ChromeDriverManager
from bson.json_util import dumps
from io import BytesIO
from fake_useragent import UserAgent
import pandas as pd
import json
from datetime import datetime, timedelta
import yaml
import hashlib
import uuid
import certifi
import requests
import random
from authlib.integrations.flask_client import OAuth
from authlib.common.security import generate_token
import os
from mongoengine.connection import get_db, connect

existing_endpoints = ["/applications", "/resume"]

user_agent = UserAgent()


def create_app():
    """
    Creates a server hosted on localhost

    :return: Flask object
    """
    app = Flask(__name__)
    # # make flask support CORS
    CORS(app)

    # get all the variables from the application.yml file
    with open("application.yml") as f:
        info = yaml.load(f, Loader=yaml.FullLoader)
        GOOGLE_CLIENT_ID = info["GOOGLE_CLIENT_ID"]
        GOOGLE_CLIENT_SECRET = info["GOOGLE_CLIENT_SECRET"]
        CONF_URL = info["CONF_URL"]
        app.secret_key = info['SECRET_KEY']

    app.config["CORS_HEADERS"] = "Content-Type"

    oauth = OAuth(app)

    @app.errorhandler(404)
    def page_not_found(e):
        """
        Returns a json object to indicate error 404

        :return: JSON object
        """
        return jsonify({"error": "Not Found"}), 404

    @app.errorhandler(405)
    # pylint: disable=C0103
    def page_not_allowed(e):
        """
        Returns a json object to indicate error 405

        :return: JSON object
        """
        return jsonify({"error": "Method not Allowed"}), 405

    @app.before_request
    def middleware():
        """
        Checks for user authorization tokens and returns message

        :return: JSON object
        """
        try:
            if request.method == "OPTIONS":
                return jsonify({"success": "OPTIONS"}), 200
            if request.path in existing_endpoints:
                headers = request.headers
                try:
                    token = headers["Authorization"].split(" ")[1]
                except:
                    return jsonify({"error": "Unauthorized"}), 401
                userid = token.split(".")[0]
                user = Users.objects(id=userid).first()

                if user is None:
                    return jsonify({"error": "Unauthorized"}), 401

                expiry_flag = False
                for tokens in user["authTokens"]:
                    if tokens["token"] == token:
                        expiry = tokens["expiry"]
                        expiry_time_object = datetime.strptime(
                            expiry, "%m/%d/%Y, %H:%M:%S"
                        )
                        if datetime.now() <= expiry_time_object:
                            expiry_flag = True
                        else:
                            delete_auth_token(tokens, userid)
                        break

                if not expiry_flag:
                    return jsonify({"error": "Unauthorized"}), 401

        except:
            return jsonify({"error": "Internal server error"}), 500

    def get_token_from_header():
        """
        Evaluates token from the request header

        :return: string
        """
        headers = request.headers
        token = headers["Authorization"].split(" ")[1]
        return token

    def get_userid_from_header():
        """
        Evaluates user id from the request header

        :return: string
        """
        headers = request.headers
        token = headers["Authorization"].split(" ")[1]
        print(token)
        userid = token.split(".")[0]
        return userid

    def delete_auth_token(token_to_delete, user_id):
        """
        Deletes authorization token of the given user from the database

        :param token_to_delete: token to be deleted
        :param user_id: user id of the current active user
        :return: string
        """
        user = Users.objects(id=user_id).first()
        auth_tokens = []
        for token in user["authTokens"]:
            if token != token_to_delete:
                auth_tokens.append(token)
        user.update(authTokens=auth_tokens)

    @app.route("/")
    @cross_origin()
    def health_check():
        return jsonify({"message": "Server up and running"}), 200

    @app.route("/users/signupGoogle")
    def signupGoogle():

        oauth.register(
            name='google',
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url=CONF_URL,
            client_kwargs={
                'scope': 'openid email profile'
            },
            nonce='foobar'
        )

        # Redirect to google_auth function
        redirect_uri = url_for('authorized', _external=True)
        print(redirect_uri)

        session['nonce'] = generate_token()
        return oauth.google.authorize_redirect(redirect_uri, nonce=session['nonce'])

    @app.route('/users/signupGoogle/authorized')
    def authorized():
        token = oauth.google.authorize_access_token()
        user = oauth.google.parse_id_token(token, nonce=session['nonce'])
        session['user'] = user

        user_exists = Users.objects(email=user["email"]).first()

        users_email = user["email"]
        full_name = user["given_name"] + " " + user["family_name"]

        if user['email_verified']:
            if user_exists is None:
                userSave = Users(
                    id=get_new_user_id(),
                    fullName=full_name,
                    email=users_email,
                    authTokens=[],
                    applications=[],
                    skills=[],
                    job_levels=[],
                    locations=[],
                    phone_number="",
                    address=""
                )
                userSave.save()
                unique_id = userSave['id']
            else:
                unique_id = user_exists['id']

        userSaved = Users.objects(email=user['email']).first()
        expiry = datetime.now() + timedelta(days=1)
        expiry_str = expiry.strftime("%m/%d/%Y, %H:%M:%S")
        token_whole = str(unique_id) + "." + token['access_token']
        auth_tokens_new = userSaved['authTokens'] + \
            [{"token": token_whole, "expiry": expiry_str}]
        userSaved.update(authTokens=auth_tokens_new)

        return redirect(f"http://127.0.0.1:3000/?token={token_whole}&expiry={expiry_str}&userId={unique_id}")

    @app.route("/users/signup", methods=["POST"])
    def sign_up():
        """
        Creates a new user profile and adds the user to the database and returns the message

        :return: JSON object
        """
        try:
            # print(request.data)
            data = json.loads(request.data)
            print(data)
            try:
                _ = data["username"]
                _ = data["password"]
                _ = data["fullName"]
            except:
                return jsonify({"error": "Missing fields in input"}), 400

            username_exists = Users.objects(username=data["username"])
            if len(username_exists) != 0:
                return jsonify({"error": "Username already exists"}), 400
            password = data["password"]
            password_hash = hashlib.md5(password.encode())
            user = Users(
                id=get_new_user_id(),
                fullName=data["fullName"],
                username=data["username"],
                password=password_hash.hexdigest(),
                authTokens=[],
                applications=[],
                skills=[],
                job_levels=[],
                locations=[],
                phone_number="",
                address="",
                institution="",
                email=""
            )
            user.save()
            # del user.to_json()["password", "authTokens"]
            return jsonify(user.to_json()), 200
        except:
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/getProfile", methods=["GET"])
    def get_profile_data():
        """
        Gets user's profile data from the database

        :return: JSON object with application data
        """
        try:
            userid = get_userid_from_header()
            user = Users.objects(id=userid).first()
            profileInformation = {}
            profileInformation["skills"] = user["skills"]
            profileInformation["job_levels"] = user["job_levels"]
            profileInformation["locations"] = user["locations"]
            profileInformation["institution"] = user["institution"]
            profileInformation["phone_number"] = user["phone_number"]
            profileInformation["address"] = user["address"]
            profileInformation["email"] = user["email"]
            profileInformation["fullName"] = user["fullName"]

            return jsonify(profileInformation)
        except:
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/updateProfile", methods=["POST"])
    def updateProfilePreferences():
        """
        Update the user profile with preferences: skills, job-level and location
        """
        try:
            print(request.data)
            userid = get_userid_from_header()
            user = Users.objects(id=userid).first()
            data = json.loads(request.data)
            print(user)

            for key in data.keys():
                user[key] = data[key]

            # if data["skills"]:
            #     user.skills = data["skills"]

            # if data["job_levels"]:
            #     user.job_levels = data["job_levels"]

            # if data["locations"]:
            #     user.locations = data["locations"]

            # if data["institution"]:
            #     user.institution = data["institution"]

            # if data["phone_number"]:
            #     user.phone_number = data["phone_number"]

            # if data["address"]:
            #     user.address = data["address"]

            user.save()
            return jsonify(user.to_json()), 200

        except Exception as err:
            print(err)
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/getRecommendations", methods=["GET"])
    def getRecommendations():
        """
        Update the user profile with preferences: skills, job-level and location
        """
        try:
            userid = get_userid_from_header()
            user = Users.objects(id=userid).first()
            print(user["skills"])
            skill_sets = [x["value"] for x in user["skills"]]
            job_levels_sets = [x["value"] for x in user["job_levels"]]
            locations_set = [x["value"] for x in user["locations"]]
            recommendedJobs = []
            headers = {"User-Agent":
                       #    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
                       user_agent.random,
                       "Referrer": "https://www.google.com/"
                       }
            if len(skill_sets) > 0 or len(job_levels_sets) > 0 or len(locations_set) > 0:
                random_skill = random.choice(skill_sets)
                random_job_level = random.choice(job_levels_sets)
                random_location = random.choice(locations_set)
                query = "https://www.google.com/search?q=" + random_skill + \
                    random_job_level + random_location + "&ibp=htl;jobs"
                print(query)


            else:
                query = "https://www.google.com/search?q=" + "sde usa" + "&ibp=htl;jobs"

            page = requests.get(query, headers=headers)
            soup = BeautifulSoup(page.text, "html.parser")
            # KGjGe - div class to get url
            mydivs = soup.find_all("div", class_="PwjeAc")
            for div in mydivs:
                job = {}
                inner_div = div.find("div", class_="KGjGe")
                if inner_div:
                    job["jobLink"] = inner_div.get("data-share-url")
                job["jobTitle"] = div.find(
                    "div", {"class": "BjJfJf PUpOsf"}).text
                print(job["jobTitle"])
                job["companyName"] = div.find("div", {"class": "vNEEBe"}).text
                job["location"] = div.find("div", {"class": "Qk80Jf"}).text
                recommendedJobs.append(job)
            print(recommendedJobs)
            return jsonify(recommendedJobs)

        except Exception as err:
            print(err)
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/users/login", methods=["POST"])
    def login():
        """
        Logs in the user and creates a new authorization token and stores in the database

        :return: JSON object with status and message
        """
        try:
            try:
                data = json.loads(request.data)
                _ = data["username"]
                _ = data["password"]
            except:
                return jsonify({"error": "Username or password missing"}), 400
            password_hash = hashlib.md5(data["password"].encode()).hexdigest()
            user = Users.objects(
                username=data["username"], password=password_hash
            ).first()
            if user is None:
                return jsonify({"error": "Wrong username or password"})
            token = str(user["id"]) + "." + str(uuid.uuid4())
            expiry = datetime.now() + timedelta(days=1)
            expiry_str = expiry.strftime("%m/%d/%Y, %H:%M:%S")
            auth_tokens_new = user["authTokens"] + [
                {"token": token, "expiry": expiry_str}
            ]
            user.update(authTokens=auth_tokens_new)
            

            profileInfo = {
                "id": user.id,
                "fullName": user.fullName,
                "institution": user.institution,
                "skills": user.skills,
                "phone_number": user.phone_number,
                "address": user.address,
                "locations": user.locations,
                "jobLevels": user.job_levels,
                "email": user.email
            }
            return jsonify({"profile": profileInfo, "token": token, "expiry": expiry_str})
        except:
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/users/logout", methods=["POST"])
    def logout():
        """
        Logs out the user and deletes the existing token from the database

        :return: JSON object with status and message
        """
        try:
            userid = get_userid_from_header()
            user = Users.objects(id=userid).first()
            auth_tokens = []
            incoming_token = get_token_from_header()
            for token in user["authTokens"]:
                if token["token"] != incoming_token:
                    auth_tokens.append(token)
            user.update(authTokens=auth_tokens)

            return jsonify({"success": ""}), 200

        except:
            return jsonify({"error": "Internal server error"}), 500

    # search function
    # params:
    #   -keywords: string
    @app.route("/search")
    def search():
        """
        Searches the web and returns the job postings for the given search filters

        :return: JSON object with job results
        """
        keywords = (
            request.args.get("keywords")
            if request.args.get("keywords")
            else "random_test_keyword"
        )
        salary = request.args.get(
            "salary") if request.args.get("salary") else ""
        keywords = keywords.replace(" ", "+")
        if keywords == "random_test_keyword":
            return json.dumps({"label": str("successful test search")})
        # create a url for a crawler to fetch job information
        if salary:
            url = (
                "https://www.google.com/search?q="
                + keywords
                + "%20salary%20"
                + salary
                + "&ibp=htl;jobs"
            )
        else:
            url = "https://www.google.com/search?q=" + keywords + "&ibp=htl;jobs"

        print(user_agent.random)
        headers = {"User-Agent":
                   #    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
                   user_agent.random,
                   "Referrer": "https://www.google.com/"
                   }

        page = requests.get(url, headers=headers)
        soup = BeautifulSoup(page.text, "html.parser")

        # parsing searching results to DataFrame and return
        df = pd.DataFrame(
            columns=["jobTitle", "jobLink","companyName", "location", "date", "qualifications", "responsibilities", "benefits"])
        mydivs = soup.find_all("div", class_="PwjeAc")

        for i, div in enumerate(mydivs):
            df.at[i, "jobTitle"] = div.find(
                "div", {"class": "BjJfJf PUpOsf"}).text
            df.at[i, "companyName"] = div.find("div", {"class": "vNEEBe"}).text
            df.at[i, "location"] = div.find("div", {"class": "Qk80Jf"}).text
            df.at[i, "date"] = div.find_all(
                "span", {"class": "LL4CDc"}, limit=1)[0].text
            
            apply_link = div.find("a", class_="pMhGee Co68jc j0vryd")
            if apply_link:
                link_href = apply_link.get("href")
                link_text = apply_link.get("title")
                clickable_link = f'<a href="{link_href}" target="_blank">{link_text}</a>'

                df.at[i, "jobLink"] = clickable_link

            # Collect Job Description Details
            desc = div.find_all("div", {"class": "JxVj3d"})
            for ele in desc:
                arr = list(x.text for x in ele.find_all(
                    "div", {"class": "nDgy9d"}))
                title = ele.find("div", {"class": "iflMsb"}).text
                if arr:
                    df.at[i, str(title).lower()] = arr
        missingCols = list(
            (df.loc[:, df.isnull().sum(axis=0).astype(bool)]).columns)

        for col in missingCols:
            df.loc[df[col].isnull(), [col]] = df.loc[df[col].isnull(
            ), col].apply(lambda x: [])
        # df.loc[df["benefits"].isnull(), ["benefits"]] = df.loc[df["benefits"].isnull(), "benefits"].apply(lambda x: [])
        return jsonify(df.to_dict("records"))

    # get data from the CSV file for rendering root page
    @app.route("/applications", methods=["GET"])
    def get_data():
        """
        Gets user's applications data from the database

        :return: JSON object with application data
        """
        try:
            userid = get_userid_from_header()
            user = Users.objects(id=userid).first()
            applications = user["applications"]
            return jsonify(applications)
        except:
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/applications", methods=["POST"])
    def add_application():
        """
        Add a new job application for the user

        :return: JSON object with status and message
        """
        try:
            userid = get_userid_from_header()
            try:
                request_data = json.loads(request.data)["application"]
                _ = request_data["jobTitle"]
                _ = request_data["companyName"]
            except:
                return jsonify({"error": "Missing fields in input"}), 400

            user = Users.objects(id=userid).first()
            current_application = {
                "id": get_new_application_id(userid),
                "jobTitle": request_data["jobTitle"],
                "companyName": request_data["companyName"],
                "date": request_data.get("date"),
                "jobLink": request_data.get("jobLink"),
                "location": request_data.get("location"),
                "status": request_data.get("status", "1"),
            }
            applications = user["applications"] + [current_application]

            user.update(applications=applications)
            return jsonify(current_application), 200
        except:
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/applications/<int:application_id>", methods=["PUT"])
    def update_application(application_id):
        """
        Updates the existing job application for the user

        :param application_id: Application id to be modified
        :return: JSON object with status and message
        """
        try:
            userid = get_userid_from_header()
            try:
                request_data = json.loads(request.data)["application"]
            except:
                return jsonify({"error": "No fields found in input"}), 400

            user = Users.objects(id=userid).first()
            current_applications = user["applications"]

            if len(current_applications) == 0:
                return jsonify({"error": "No applications found"}), 400
            else:
                updated_applications = []
                app_to_update = None
                application_updated_flag = False
                for application in current_applications:
                    if application["id"] == application_id:
                        app_to_update = application
                        application_updated_flag = True
                        for key, value in request_data.items():
                            application[key] = value
                    updated_applications += [application]
                if not application_updated_flag:
                    return jsonify({"error": "Application not found"}), 400
                user.update(applications=updated_applications)

            return jsonify(app_to_update), 200
        except:
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/applications/<int:application_id>", methods=["DELETE"])
    def delete_application(application_id):
        """
        Deletes the given job application for the user

        :param application_id: Application id to be modified
        :return: JSON object with status and message
        """
        try:
            userid = get_userid_from_header()
            user = Users.objects(id=userid).first()

            current_applications = user["applications"]

            application_deleted_flag = False
            updated_applications = []
            app_to_delete = None
            for application in current_applications:
                if application["id"] != application_id:
                    updated_applications += [application]
                else:
                    app_to_delete = application
                    application_deleted_flag = True

            if not application_deleted_flag:
                return jsonify({"error": "Application not found"}), 400
            user.update(applications=updated_applications)
            return jsonify(app_to_delete), 200
        except:
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/resume", methods=["POST"])
    def upload_resume():
        """
        Uploads resume file or updates an existing resume for the user

        :return: JSON object with status and message
        """
        try:
            userid = get_userid_from_header()
            try:
                file = request.files["file"]  # .read()
            except:
                return jsonify({"error": "No resume file found in the input"}), 400

            user = Users.objects(id=userid).first()
            if not user.resume.read():
                # There is no file
                user.resume.put(file, filename=file.filename,
                                content_type="application/pdf")
                user.save()
                return jsonify({"message": "resume successfully uploaded"}), 200
            else:
                # There is a file, we are replacing it
                user.resume.replace(
                    file, filename=file.filename, content_type="application/pdf")
                user.save()
                return jsonify({"message": "resume successfully replaced"}), 200
        except Exception as e:
            print(e)
            return jsonify({"error": "Internal server error"}), 500

    @app.route("/resume", methods=["GET"])
    def get_resume():
        """
        Retrieves the resume file for the user

        :return: response with file
        """
        try:
            userid = get_userid_from_header()
            try:
                user = Users.objects(id=userid).first()
                if len(user.resume.read()) == 0:
                    raise FileNotFoundError
                else:
                    user.resume.seek(0)

            except:
                return jsonify({"error": "resume could not be found"}), 400

            filename = user.resume.filename
            content_type = user.resume.contentType
            response = send_file(
                user.resume,
                mimetype=content_type,
                download_name=filename,
                as_attachment=True,
            )
            response.headers["x-filename"] = filename
            response.headers["Access-Control-Expose-Headers"] = "x-filename"
            return response, 200
        except:
            return jsonify({"error": "Internal server error"}), 500

    return app


app = create_app()


with open("application.yml") as f:
    info = yaml.load(f, Loader=yaml.FullLoader)
    username = info["username"]
    password = info["password"]
    # ca=certifi.where()
    app.config["MONGODB_SETTINGS"] = {
        "db": "appTracker",
        
        # "host": f"mongodb+srv://{username}:{password}@cluster0.r0056lg.mongodb.net/appTracker?tls=true&tlsCAFile={certifi.where()}&retryWrites=true&w=majority",
        "host": f"mongodb+srv://{username}:{password}@seproject.p1kzzaz.mongodb.net/?tls=true&tlsCAFile={certifi.where()}&retryWrites=true&w=majority",

    }
db = MongoEngine()
db.init_app(app)


class Users(db.Document):
    """
    Users class. Holds full name, username, password, as well as applications and resumes
    """

    id = db.IntField(primary_key=True)
    fullName = db.StringField()
    username = db.StringField()
    password = db.StringField()
    authTokens = db.ListField()
    email = db.StringField()
    applications = db.ListField()
    resume = db.FileField()
    skills = db.ListField()
    job_levels = db.ListField()
    locations = db.ListField()
    institution = db.StringField()
    phone_number = db.StringField()
    address = db.StringField()

    def to_json(self):
        """
        Returns the user details in JSON object

        :return: JSON object
        """
        return {"id": self.id, "fullName": self.fullName, "username": self.username}


def get_new_user_id():
    """
    Returns the next value to be used for new user

    :return: key with new user_id
    """
    user_objects = Users.objects()
    if len(user_objects) == 0:
        return 1

    new_id = 0
    for a in user_objects:
        new_id = max(new_id, a["id"])

    return new_id + 1


def get_new_application_id(user_id):
    """
    Returns the next value to be used for new application

    :param: user_id: User id of the active user
    :return: key with new application_id
    """
    user = Users.objects(id=user_id).first()

    if len(user["applications"]) == 0:
        return 1

    new_id = 0
    for a in user["applications"]:
        new_id = max(new_id, a["id"])

    return new_id + 1

# def build_preflight_response():
    # response = make_response()
    # response.headers.add("Access-Control-Allow-Origin", "*")
    # response.headers.add('Access-Control-Allow-Headers', "*")
    # response.headers.add('Access-Control-Allow-Methods', "*")
    # return response
# def build_actual_response(response):
    # response.headers.add("Access-Control-Allow-Origin", "*")
    # return response


if __name__ == "__main__":
    app.run()
    
