import datetime
import cv2
import keras
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
import mediapipe as mp
from gaze_tracking import GazeTracking, GazeTracking_SelfieFaces
from module_name.models import Feedback, Database

# Configure TensorFlow to use a specific fraction of GPU memory
config = tf.compat.v1.ConfigProto()
config.gpu_options.per_process_gpu_memory_fraction = 0.70
sess = tf.compat.v1.Session(config=config)
tf.compat.v1.keras.backend.set_session(sess)

# Check for GPU availability
if tf.test.gpu_device_name():
    print("GPU found")
    keras.backend.set_image_data_format("channels_last")
    tf.config.experimental.set_memory_growth(
        tf.config.experimental.list_physical_devices("GPU")[0], True
    )
else:
    print("No GPU found")

# Initialize GazeTracking for head and eye gesture analysis
headTracking = GazeTracking()
headTrackingSelfie = GazeTracking_SelfieFaces()

# Initialize MediaPipe hands for hand gesture analysis
mpHands = mp.solutions.hands
hands = mpHands.Hands(
    static_image_mode=False,
    model_complexity=1,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
    max_num_hands=2,
)

# Load a default image for reference
def_image = cv2.imread("media/myImages/meFar.jpg")


class MovementAnalysis:
    def head_and_eyes_analysis(
        self, video_url="video", ratioX=0.95, ratioY=0.05, itechnolabs_id=67
    ):
        """
        Analyzes head and eye movements from a video stream.

        Parameters:
            video_url (str): The URL of the video to analyze.
            ratioX (float): Weight for head movements in the x-direction.
            ratioY (float): Weight for eye movements in the y-direction.
            itechnolabs_id (int): ID of the itechnolabs for feedback storage.

        Returns:
            dict: A dictionary containing analysis results.
            int: A flag indicating analysis status.
        """

        def focal_length(measured_distance, real_width, width_in_rf_image):
            """
            Calculates the focal length of the camera.

            Parameters:
                measured_distance (float): Distance from the camera to the object.
                real_width (float): Real width of the object.
                width_in_rf_image (float): Width of the object in the reference image.

            Returns:
                float: Calculated focal length.
            """
            return (width_in_rf_image * measured_distance) / real_width

        def distance_finder(focal_length, real_face_width, face_width_in_frame):
            """
            Calculates the distance from the camera to the face.

            Parameters:
                focal_length (float): Focal length of the camera.
                real_face_width (float): Real width of the face.
                face_width_in_frame (float): Width of the face in the frame.

            Returns:
                float: Calculated distance.
            """
            return (real_face_width * focal_length) / face_width_in_frame

        def head_movements(vertical_ratio, horizontal_ratio):
            """
            Determines the head movement direction based on ratios.

            Parameters:
                vertical_ratio (float): Ratio of vertical movement.
                horizontal_ratio (float): Ratio of horizontal movement.

            Returns:
                str: Direction of head movement.
            """
            text_head = ""
            if horizontal_ratio >= 0.71 and horizontal_ratio <= 1.45:
                if vertical_ratio >= 1.33 and vertical_ratio <= 2.10:
                    text_head = "Center"
                elif vertical_ratio < 1.33:
                    text_head = "Upper"
                elif vertical_ratio > 2.10:
                    text_head = "Down"
            elif horizontal_ratio < 0.71:
                text_head = "Left"
            elif horizontal_ratio > 1.45:
                text_head = "Right"
            return text_head

        def get_eyes_data(
            image,
            valuePix,
            known_distance=43,
            known_width=12.3,
            mindistance_selfie=20,
            mindistance_fullbody=69,
            maxdistance_fullbody=140,
        ):
            """
            Analyzes eye movements based on the input image.

            Parameters:
                image (ndarray): The input image for analysis.
                valuePix (float): Brightness value of the image.
                known_distance (float): Known distance for focal length calculation.
                known_width (float): Known width for focal length calculation.
                mindistance_selfie (float): Minimum distance for selfie mode.
                mindistance_fullbody (float): Minimum distance for full body mode.
                maxdistance_fullbody (float): Maximum distance for full body mode.

            Returns:
                tuple: Analysis results including low light warnings, eye movements, head analysis, and distance warnings.
            """
            # Obtain face data
            face_width, face_values = headTracking.face_data(image)
            face_width_img, face_values_img = headTracking.face_data(def_image)
            low_light_text = ""
            text_eyes_movements = ""
            text_head_analysis = ""

            if face_values <= 65:
                low_light_text = "Low Light Found"

            focal_length_found = focal_length(
                known_distance, known_width, face_width_img
            )

            if face_width != 0:
                distance = distance_finder(focal_length_found, known_width, face_width)

                if mindistance_selfie < distance < mindistance_fullbody:
                    vertical_ratio, horizontal_ratio = headTrackingSelfie.refresh(image)
                    text_head_analysis = head_movements(
                        vertical_ratio, horizontal_ratio
                    )

                    if valuePix > 97:
                        if headTrackingSelfie.is_right():
                            text_eyes_movements = "Right"
                        elif headTrackingSelfie.is_left():
                            text_eyes_movements = "Left"
                        elif headTrackingSelfie.is_center():
                            text_eyes_movements = "Center"

                if mindistance_fullbody <= distance <= maxdistance_fullbody:
                    vertical_ratio, horizontal_ratio = headTracking.refresh(image)
                    text_head_analysis = head_movements(
                        vertical_ratio, horizontal_ratio
                    )

                    if valuePix > 97:
                        if headTracking.is_right():
                            text_eyes_movements = "Right"
                        elif headTracking.is_left():
                            text_eyes_movements = "Left"
                        elif headTracking.is_center():
                            text_eyes_movements = "Center"

            text_yourfar = ""
            if distance > maxdistance_fullbody:
                text_yourfar = "You Are Too Far"

            return low_light_text, text_eyes_movements, text_head_analysis, text_yourfar

        # Initialize lists for analysis results
        head, eyes, errors, gestures = [], [], [], []
        flag, dt = 0, {}
        cap = cv2.VideoCapture(video_url)
        length = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        print("Frame count", length)

        count = 1
        while True:
            try:
                _, frame = cap.read()
                if frame is None:
                    break
            except Exception as e:
                print(e)
                break

            try:
                # Resize the frame for processing
                frame_light = frame.mean()
                frame = cv2.resize(frame, (480, 360))
            except Exception as e:
                frame_light = None
                print(e)

            try:
                image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                resultt = hands.process(image)
                if resultt.multi_hand_landmarks:
                    print("Hand is present")
                    gestures.append(count)
            except Exception as e:
                print(e)

            count += 1

            try:
                low_light_text, text_eyes_movements, text_head_results, your_toofar = (
                    get_eyes_data(frame, frame_light)
                )
                if low_light_text:
                    errors.append(low_light_text)
                if your_toofar:
                    errors.append(your_toofar)
                if text_eyes_movements:
                    eyes.append(text_eyes_movements)
                if text_head_results:
                    head.append(text_head_results)

            except IndexError:
                print("IndexError: No Face Found")
                errors.append("No Face Found")

        # Compile results
        headAndeyes_results = head + eyes
        results = headAndeyes_results + gestures + errors
        total = len(results)

        # Analyze gestures and provide feedback
        length_1 = len(gestures) if gestures else 0
        count_1, count_2 = 0, 0
        half_length = round(length / 2)

        for i in gestures:
            if i <= half_length:
                count_1 += 1
            if half_length < i < length:
                count_2 += 1

        statement1 = self.gesture_score(count_1, half_length)
        statement2 = self.gesture_score(count_2, length - half_length)

        # Count occurrences of specific results
        no_facefound = results.count("No Face Found")
        low_lightfound = results.count("Low Light Found")
        your_toofar = results.count("You Are Too Far")

        # Set flags based on analysis
        if low_lightfound > total * 0.50:
            flag = 1
        if your_toofar > total * 0.50:
            flag = 2
        if no_facefound > total * 0.50:
            flag = 3

        # Calculate weighted averages for head and eye movements
        if flag not in [1, 2, 3]:
            head_center = head.count("Center")
            head_up = head.count("Upper")
            head_down = head.count("Down")
            head_left = head.count("Left")
            head_right = head.count("Right")
            eyes_left = eyes.count("Left")
            eyes_right = eyes.count("Right")
            eyes_center = eyes.count("Center")

            weighted_avgCenter = int(round(head_center * ratioX, 1)) + int(
                round(eyes_center * ratioY, 1)
            )
            weighted_avgLeft = int(round(head_left * ratioX, 1)) + int(
                round(eyes_left * ratioY, 1)
            )
            weighted_avgright = int(round(head_right * ratioX, 1)) + int(
                round(eyes_right * ratioY, 1)
            )

            dt = {
                "center": weighted_avgCenter,
                "up": head_up,
                "down": head_down,
                "left": weighted_avgLeft,
                "right": weighted_avgright,
                "total_gesture": length_1,
                "first_gesture": count_1,
                "second_gesture": count_2,
            }

            self.feedback(itechnolabs_id, dt)

        return dt, flag

    def feedback(self, itechnolabs_id, dt):
        """
        Saves feedback to the database based on itechnolabs ID.

        Parameters:
            itechnolabs_id (int): ID of the itechnolabs.
            dt (dict): Data containing feedback information.

        Returns:
            Feedback: The saved feedback object.
        """
        p_id = Database.objects.get(id=itechnolabs_id)
        try:
            queryset = Feedback.objects.get(itechnolabs_id=p_id)
        except Feedback.DoesNotExist:
            queryset = Feedback(itechnolabs_id=p_id)

        for key, value in dt.items():
            setattr(queryset, key, value)
        queryset.save()
        return queryset

    def gesture_score(self, score, n_samples):
        """
        Calculates a score for gestures based on the number of samples.

        Parameters:
            score (int): The score to evaluate.
            n_samples (int): The number of samples analyzed.

        Returns:
            str: A statement providing feedback on gesture usage.
        """
        bar1, bar2, bar3, bar4 = (
            [0, 100, 200, 300]
            if n_samples < 750
            else (
                [0, 200, 400, 600] if 750 <= n_samples <= 1250 else [0, 400, 800, 1600]
            )
        )

        if score == 0:
            return "We didn’t see any gestures. Is this a missed opportunity? Consider using gestures to help you emphasize your key messages."
        elif 1 <= score <= bar2:
            return "Good effort! You had some gestures, try using a few more."
        elif bar2 + 1 <= score <= bar3:
            return "Awesome, that was magnificent! You are using your gestures to add interest."
        elif bar3 + 1 <= score <= bar4:
            return "Phew! We can see you are using your hands. Maybe a little too much."
        else:
            return "Ouch! There were a lot of gestures."

    def graph_pie(self, labels, sizes, col, explode, path, gtype):
        """
        Draws and saves a pie chart based on the provided data.

        Parameters:
            labels (list): Labels for the pie chart segments.
            sizes (list): Sizes of each segment.
            col (list): Colors for each segment.
            explode (list): Fraction to offset each segment.
            path (str): Path to save the pie chart.
            gtype (str): Type of graph being drawn.

        Returns:
            str: Path to the saved pie chart, or None if unsuccessful.
        """
        try:
            if sizes is None or all(size == 0 for size in sizes):
                print("No data to display.")
                return None

            sizes = np.array(sizes)
            sizes = sizes[np.isfinite(sizes)]

            if sizes.size == 0:
                print("No valid data to display.")
                return None

            if len(sizes) != len(labels):
                print("Number of sizes and labels must be the same.")
                return None

            sizes = sizes.ravel()
            plt.pie(sizes, colors=col, startangle=90, shadow=False, explode=explode)
            total = np.sum(sizes)

            if total == 0:
                return None

            if gtype == "gesture":
                labels = ["%s, %1.1f" % (l, (int(s))) for l, s in zip(labels, sizes)]
            else:
                labels = [
                    "%s, %1.1f%%" % (l, (float(s) / total) * 100)
                    for l, s in zip(labels, sizes)
                ]

            plt.legend(bbox_to_anchor=(0.00, 1), labels=labels)
            plt.savefig(path, dpi=400, bbox_inches="tight", pad_inches=0)
            plt.close("all")
        except Exception as e:
            path = None
            print(datetime.datetime.now().isoformat(), "!! Error:", e)

        return path
